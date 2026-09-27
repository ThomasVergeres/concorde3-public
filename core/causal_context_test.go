package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func TestCausalActualHarnessWritebackRestartLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Actual command harness phases", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestCausalCommandHelper", "causal-loop"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Mutate("operator", "Applicable history", applicableHistory(32)); err != nil {
		t.Fatal(err)
	}
	if err := s.Notify("purpose", "arrival", "Current source changed"); err != nil {
		t.Fatal(err)
	}
	if err := s.Run(context.Background(), true, nil); err != nil {
		t.Fatal(err)
	}
	s = Open(s.Dir)
	if err := s.Run(context.Background(), true, nil); err != nil {
		t.Fatal(err)
	}
	st, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	if len(st.Activations) != 2 {
		t.Fatal("expected two embodiments")
	}
	for _, a := range st.Activations {
		if a.Status != "completed" {
			t.Fatal(a.Summary)
		}
	}
}

func TestCausalCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "causal-loop") {
		return
	}
	if os.Args[len(os.Args)-1] == "--concorde-capabilities" {
		fmt.Print(`{"protocol":2,"continuation":true}`)
		os.Exit(0)
	}
	var p Packet
	if err := json.NewDecoder(os.Stdin).Decode(&p); err != nil {
		panic(err)
	}
	st, err := Open(os.Getenv("CONCORDE3_INSTANCE")).Read()
	if err != nil {
		panic(err)
	}
	if len(st.Activations) == 1 && (p.ObservationBatch == nil || p.ObservationBatch.Total != 1 || len(p.Wakes) != 1) {
		panic("causal evidence lost in actual phase")
	}
	if len(st.Activations) == 2 && len(p.Wakes) != 0 {
		panic("already consumed evidence resurrected")
	}
	out := Outcome{Summary: "Handled the visible encounter"}
	if p.Phase == "rectification" {
		out.Completion = &Completion{ExpectedSeq: st.Seq, Continuation: "continue", Coverage: "Next useful work", Reason: "Encounter integrated"}
	}
	_ = json.NewEncoder(os.Stdout).Encode(out)
	os.Exit(0)
}

func TestCausalObservationFloodHasBoundedDiscoverableOverflow(t *testing.T) {
	s := fixture(t)
	running(t, s)
	if err := s.Update("operator", "fixture", "Many observations", func(st *State) error {
		for j := 0; j < 100; j++ {
			id := fmt.Sprintf("wake.%03d", j)
			st.Wakes[id] = Wake{ID: id, Pursuit: "purpose", At: s.Now(), Evidence: strings.Repeat("Evidence ", 400)}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	a, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if p.ObservationBatch == nil || p.ObservationBatch.Total != 100 || p.ObservationBatch.Omitted != 96 || !p.ObservationBatch.Truncated || len(p.Wakes) != 4 {
		t.Fatal("lost or unbounded observations", p.ObservationBatch)
	}
	value, err := s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{"query": "current situation"}))
	if err != nil {
		t.Fatal(err)
	}
	if len(Marshal(value)) > a.Config.ResponseBytes || value.(Packet).ObservationBatch.Total != 100 {
		t.Fatal("bad bounded refresh")
	}
	full, err := s.Call(context.Background(), a.ID, "record", Marshal(map[string]any{"section": "wakes", "id": "wake.099"}))
	if err != nil || !strings.Contains(string(Marshal(full)), "Evidence") {
		t.Fatal("overflow inaccessible", err)
	}
	st, _ := s.Read()
	if len(st.Wakes["wake.000"].Evidence) != 3600 {
		t.Fatal("canonical evidence truncated")
	}
}

func TestRequiredContextFailureDoesNotConsumeWake(t *testing.T) {
	s := fixture(t)
	running(t, s)
	if err := s.Mutate("operator", "Populate explicit bindings", applicableHistory(20)); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "fixture", "Explicit bindings exceed available context", func(st *State) error {
		for id := range st.Items {
			st.Config.Global = append(st.Config.Global, id)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Notify("purpose", "retained", "Current evidence"); err != nil {
		t.Fatal(err)
	}
	_, _, err := s.Admit()
	if err == nil {
		t.Fatal("oversized essentials admitted")
	}
	st, _ := s.Read()
	if st.Wakes["retained"].ConsumedBy != "" || len(st.Activations) != 0 {
		t.Fatal("failed admission consumed evidence")
	}
}

func TestCausalObservationSurvivesCrowdingAndRefresh(t *testing.T) {
	s := fixture(t)
	running(t, s)
	if err := s.Mutate("operator", "Accrued applicable history", applicableHistory(40)); err != nil {
		t.Fatal(err)
	}
	if err := s.Notify("purpose", "customer-change", "Current source /exchange/messages.json changed; inspect it before deciding what remains."); err != nil {
		t.Fatal(err)
	}
	a, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	check := func(p Packet) {
		t.Helper()
		if len(p.Wakes) == 0 || p.Wakes[0].ID != "customer-change" {
			t.Fatalf("consumed triggering evidence omitted: phase=%s omitted=%d observations=%v", p.Phase, p.Omitted, p.Wakes)
		}
		if p.Error != "" {
			t.Fatal(p.Error)
		}
	}
	check(p)
	refresh := func() {
		t.Helper()
		value, e := s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{"query": "customer feedback"}))
		if e != nil {
			t.Fatal(e)
		}
		check(value.(Packet))
		if len(Marshal(value)) > a.Config.ResponseBytes {
			t.Fatal("response exceeds bound")
		}
	}
	refresh()
	if err = s.BeginRectification(a.ID, "Inspected current evidence", "session"); err != nil {
		t.Fatal(err)
	}
	refresh()
	now := s.Now().Add(time.Second)
	s = Open(s.Dir)
	s.Now = func() time.Time { return now }
	refresh()
}

func TestQuietContextDoesNotInventObservations(t *testing.T) {
	s := fixture(t)
	running(t, s)
	_, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Wakes) != 0 {
		t.Fatal("invented trigger")
	}
}

func TestCausalPreviewAdaptsToLargeRequiredBinding(t *testing.T) {
	s := fixture(t)
	running(t, s)
	if err := s.Mutate("operator", "Additional required operating context", Batch{Nodes: []NodeChange{{Node: Node{ID: "environment", Title: "Operating environment", Status: "active"}}}, Items: []ItemChange{{Item: Item{ID: "environment-access", Node: "environment", Kind: "norm", Status: "active", Text: strings.Repeat("Operating context. ", 185)}}}}); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "config", "Required environment binding", func(st *State) error { st.Config.Global = append(st.Config.Global, "environment-access"); return nil }); err != nil {
		t.Fatal(err)
	}
	for j := 0; j < 4; j++ {
		if err := s.Notify("purpose", fmt.Sprintf("wake.%d", j), strings.Repeat("evidence ", 400)); err != nil {
			t.Fatal(err)
		}
	}
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	value, err := s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{"query": "operating context"}))
	if err != nil {
		t.Fatal(err)
	}
	p := value.(Packet)
	if p.Error != "" || len(p.Wakes) == 0 || p.ObservationBatch.Total != 4 || len(Marshal(p)) > a.Config.ResponseBytes {
		t.Fatal("lost required context or evidence", p.Error)
	}
}
