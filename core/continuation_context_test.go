package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

func TestContinuationActualHarnessRestartLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Two actual harness loops", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestContinuationCommandHelper", "continuation-fixture"}
		return nil
	}); err != nil {
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

func TestContinuationCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "continuation-fixture") {
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
	if len(st.Activations) > 1 && (p.Handoff == nil || p.Handoff.Decision.Reason != "Use the accumulated result") {
		panic("fresh harness did not receive previous direction")
	}
	out := Outcome{Summary: "Worked on the retained direction"}
	if p.Phase == "rectification" {
		out.Completion = &Completion{ExpectedSeq: st.Seq, Continuation: "continue", Coverage: "Next stage remains", Reason: "Use the accumulated result"}
	}
	_ = json.NewEncoder(os.Stdout).Encode(out)
	os.Exit(0)
}

func TestContinuationSurvivesAdmissionRestartAndAcknowledgment(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		t.Run(fmt.Sprintf("work-return=%v", enabled), func(t *testing.T) { continuationSurvives(t, enabled) })
	}
}

func continuationSurvives(t *testing.T, workReturn bool) {
	s := fixture(t)
	if err := s.Update("operator", "config", "Qualify optional context footprint", func(st *State) error { st.Config.WorkReentry = workReturn; return nil }); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Update(a.ID, "fixture", "Acknowledge without resolving", func(st *State) error {
		st.Consequences["next"] = Consequence{ID: "next", Summary: "Try another authorized approach", References: []string{"purpose"}, Targets: []string{"purpose"}, Actor: a.ID, CreatedAt: s.Now(), Acknowledgments: map[string]string{"purpose": "Considered, still unresolved"}}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Checked inbox; no new feedback", "session"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	if err = s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Outstanding: []string{"next"}, Continuation: "continue", Coverage: "Try a different validation step", Reason: "Repeated checks have yielded nothing"}); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "A different approach remains"}, nil); err != nil {
		t.Fatal(err)
	}
	now := s.Now().Add(time.Minute)
	s = Open(s.Dir)
	s.Now = func() time.Time { return now }
	b, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if p.Handoff == nil || p.Handoff.Activation != a.ID || p.Handoff.Decision.Coverage != "Try a different validation step" || len(p.Consequences) != 1 {
		t.Fatalf("lost direction: %+v", p)
	}
	if len(p.Recent) != 1 || p.Recent[0].ReportedOutcome != "Checked inbox; no new feedback" {
		t.Fatal("lost trajectory", p.Recent)
	}
	if err = s.Mutate(b.ID, "Accrued observations", applicableHistory(32)); err != nil {
		t.Fatal(err)
	}
	v, err := s.Call(context.Background(), b.ID, "context", Marshal(map[string]any{"query": "customer feedback"}))
	if err != nil {
		t.Fatal(err)
	}
	refreshed := v.(Packet)
	if refreshed.Handoff == nil || len(refreshed.Consequences) != 1 || len(Marshal(v)) > b.Config.ResponseBytes {
		t.Fatal("crowding displaced continuity")
	}
	// A later deliberate disposition removes the outstanding status without
	// erasing the acknowledged consequence or enforcing the predecessor's choice.
	complete(t, s, b, "continue")
	now = now.Add(time.Minute)
	_, p, err = s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Consequences) != 0 || p.Handoff.Activation != b.ID {
		t.Fatal("historical outstanding resurrected")
	}
}

func TestContinuityBoundsAndCanonicalHistory(t *testing.T) {
	s := fixture(t)
	st, _ := s.Read()
	now := s.Now()
	for j := 0; j < 9; j++ {
		id := strings.Repeat("a", j+1)
		st.Activations[id] = Activation{ID: id, Pursuit: "purpose", Status: "completed", Started: now.Add(-time.Hour), Finished: now.Add(time.Duration(-j-1) * time.Minute), WorkSummary: strings.Repeat("你好", 500), Completion: &Completion{Continuation: "wait", Reason: strings.Repeat("x", 3000), Coverage: "No new evidence"}}
	}
	a := Activation{ID: "new", Pursuit: "purpose", Phase: "work", Started: now}
	before := Digest(Marshal(st))
	p := Assemble(st, a)
	if len(p.Recent) == 0 || len(p.Recent) > 4 || p.Omitted == 0 || len(Marshal(p)) > st.Config.ContextBytes || p.Handoff == nil {
		t.Fatalf("unbounded or missing view: %+v", p)
	}
	for _, x := range p.Recent {
		if !utf8.ValidString(x.ReportedOutcome) || !x.Truncated {
			t.Fatal("bad truncation")
		}
	}
	if before != Digest(Marshal(st)) {
		t.Fatal("projection mutated canonical history")
	}
	st.Activations["future"] = Activation{ID: "future", Pursuit: "purpose", Finished: now.Add(time.Minute), Status: "completed", Completion: &Completion{Reason: "future"}}
	if Assemble(st, a).Handoff.Activation == "future" {
		t.Fatal("future completion leaked")
	}
}
