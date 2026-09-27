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

// Dispatch accumulated small observations, all applicable to its purpose. Node
// bounds held, but compulsory injection prevented both rectification and recovery.
func applicableHistory(count int) Batch {
	b := Batch{}
	for j := 0; j < count; j++ {
		id := fmt.Sprintf("delivery-%03d", j)
		b.Nodes = append(b.Nodes, NodeChange{Node: Node{ID: id, Title: "Delivery evidence", Status: "active"}})
		i := itemFixture(id+"-observation", strings.Repeat("Verified delivery evidence; retain original source. ", 24))
		i.Node, i.Kind, i.AppliesTo = id, "observation", []string{"purpose"}
		b.Items = append(b.Items, ItemChange{Item: i})
	}
	return b
}

func TestApplicableHistoryCannotDeadlockRectificationRecovery(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Mutate(a.ID, "Observed deliveries", applicableHistory(32)); err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Delivery effects already committed", "session"); err != nil {
		t.Fatal(err)
	}
	p, err := s.RefreshContext(context.Background(), a.ID, "")
	if err != nil || p.Error != "" || p.Omitted == 0 || len(Marshal(p)) > a.Config.ContextBytes {
		t.Fatalf("cannot rectify bounded history: %v, error=%q omitted=%d bytes=%d", err, p.Error, p.Omitted, len(Marshal(p)))
	}
	if err = s.Finish(a.ID, Outcome{}, fmt.Errorf("interrupted rectification")); err != nil {
		t.Fatal(err)
	}
	r := Open(s.Dir)
	now := s.Now().Add(2 * time.Minute)
	r.Now = func() time.Time { return now }
	b, p, err := r.Admit()
	if err != nil || b.RecoveryOf != a.ID || p.Omitted == 0 {
		t.Fatalf("recovery admission: %v %+v", err, b)
	}
	v, err := r.Call(context.Background(), b.ID, "context", Marshal(map[string]any{"query": "delivery"}))
	if err != nil || len(Marshal(v)) > b.Config.ResponseBytes {
		t.Fatalf("bounded recovery tools: %v", err)
	}
	packet := v.(Packet)
	seen := map[string]bool{}
	for _, x := range packet.Items {
		seen[x.Item.ID] = true
	}
	for _, id := range []string{"purpose", "capabilities", "rectification-practice"} {
		if !seen[id] {
			t.Fatalf("lost required context %s", id)
		}
	}
	st, _ := r.Read()
	for id, i := range st.Items {
		if !seen[id] && strings.HasPrefix(id, "delivery-") {
			if _, err = r.Call(context.Background(), b.ID, "item", Marshal(map[string]any{"id": id})); err != nil {
				t.Fatal(err)
			}
			i.Status = "retired"
			if _, err = r.Call(context.Background(), b.ID, "mutate", Marshal(map[string]any{"reason": "Retain obsolete evidence in history", "changes": Batch{Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}}})); err != nil {
				t.Fatal(err)
			}
			break
		}
	}
	complete(t, r, b, "continue")
	r = Open(s.Dir)
	r.Now = func() time.Time { return now }
	c, _, err := r.Admit()
	if err != nil || c.Phase != "work" {
		t.Fatalf("subsequent work: %v", err)
	}
	complete(t, r, c, "wait")
	st, _ = r.Read()
	if len(st.Items) != 36 || st.Activations[b.ID].Status != "completed" {
		t.Fatal("lost history or recovery")
	}
}

func TestApplicableHistoryActualHarnessLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Mutate("operator", "Accrued applicable observations", applicableHistory(32)); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "config", "Protocol-2 fixture", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestCommandHelper", "command-fixture"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Run(context.Background(), true, nil); err != nil {
		t.Fatal(err)
	}
	st, err := Open(s.Dir).Read()
	if err != nil || st.Items["subprocess"].ID == "" {
		t.Fatal("durable harness writeback", err)
	}
	for _, a := range st.Activations {
		if a.Completion == nil || a.Status != "completed" {
			t.Fatal("incomplete harness loop", a)
		}
	}
}

func TestInternalContextAndToolBudgetsAreIndependent(t *testing.T) {
	s := fixture(t)
	if err := s.Mutate("operator", "Accrued relevant history", applicableHistory(32)); err != nil {
		t.Fatal(err)
	}
	// Remove applicability here to isolate the previous internal/tool budget coupling.
	if err := s.Update("operator", "fixture", "Ordinary candidates", func(st *State) error {
		for id, i := range st.Items {
			i.AppliesTo = nil
			st.Items[id] = i
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.RefreshContext(context.Background(), a.ID, "delivery")
	if err != nil {
		t.Fatal(err)
	}
	v, err := s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{"query": "delivery"}))
	if err != nil {
		t.Fatal(err)
	}
	if len(Marshal(p)) <= a.Config.ResponseBytes || len(Marshal(p)) > a.Config.ContextBytes || len(Marshal(v)) > a.Config.ResponseBytes {
		t.Fatalf("coupled budgets: internal=%d tool=%d", len(Marshal(p)), len(Marshal(v)))
	}
}

func TestRecordedDispatchContextReplay(t *testing.T) {
	path := os.Getenv("C3_CONTEXT_REPLAY")
	if path == "" {
		t.Skip("optional private, read-only incident replay")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var st State
	if err = json.Unmarshal(data, &st); err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, a := range st.Activations {
		if !strings.Contains(a.Summary, "context exceeds") {
			continue
		}
		a.Phase = "rectification"
		for _, limit := range []int{st.Config.ContextBytes, st.Config.ResponseBytes - 1024} {
			view := clone(st)
			view.Config.ContextBytes = limit
			p := Assemble(view, a)
			if p.Error != "" || len(Marshal(p)) > limit {
				t.Fatalf("incident replay: %s limit=%d: %s", a.ID, limit, p.Error)
			}
		}
		count++
	}
	if count == 0 {
		t.Fatal("fixture contains no relevant incident")
	}
}

func TestRequiredGlobalContextStillFailsClosed(t *testing.T) {
	s := fixture(t)
	if err := s.Mutate("operator", "History", applicableHistory(32)); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "config", "Deliberately overloaded operator bindings", func(st *State) error {
		for id := range st.Items {
			st.Config.Global = append(st.Config.Global, id)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	if _, _, err := s.Admit(); err == nil {
		t.Fatal("silently dropped required operator context")
	}
	st, _ := s.Read()
	if len(st.Activations) != 0 {
		t.Fatal("charged a start without admissible context")
	}
}

func TestSaturatedMediatedContextRemainsUsable(t *testing.T) {
	s := fixture(t)
	if err := s.Mutate("operator", "History", applicableHistory(32)); err != nil {
		t.Fatal(err)
	}
	norm := itemFixture("zz-engineering-norm", "Do not disclose customer data")
	norm.Kind, norm.AppliesTo = "norm", []string{"engineering"}
	if err := s.Mutate("operator", "Applicable domain norm", Batch{Items: []ItemChange{{Item: norm}}}); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "config", "Workspace permission", func(st *State) error { st.Config.Workspace = true; return nil }); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	args := Marshal(map[string]any{"path": "probe.json", "write": "{}"})
	v, err := s.Call(context.Background(), a.ID, "artifact", args)
	if err != nil || len(Marshal(v)) > a.Config.ResponseBytes {
		t.Fatalf("deferral failed: %v", err)
	}
	m := v.(map[string]any)
	if m["deferred"] != true {
		t.Fatal("effect preceded context")
	}
	found := false
	for _, x := range m["context"].(Packet).Items {
		found = found || x.Item.ID == norm.ID
	}
	if !found {
		t.Fatal("applicable norm lost behind history")
	}
	if _, err = s.Call(context.Background(), a.ID, "artifact", args); err != nil {
		t.Fatal("action retry failed", err)
	}
	complete(t, s, a, "wait")
}
