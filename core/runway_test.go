package core

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestInterleavedMediatedWritesRetainDeliveredContext(t *testing.T) {
	s := fixture(t)
	if err := s.Update("operator", "config", "workspace", func(st *State) error { st.Config.Workspace = true; return nil }); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	write := func(path string) any {
		v, e := s.Call(context.Background(), a.ID, "artifact", Marshal(map[string]any{"path": path, "write": "{}"}))
		if e != nil {
			t.Fatal(e)
		}
		return v
	}
	write("first.json")
	write("second.json")
	write("first.json")
	write("second.json")
	for _, path := range []string{"first.json", "second.json"} {
		if _, e := os.Stat(filepath.Join(s.Dir, "artifacts", path)); e != nil {
			t.Fatal("previously delivered context was lost between actions", e)
		}
	}
	complete(t, s, a, "continue")
	r := Open(s.Dir)
	r.Now = s.Now
	b, _, err := r.Admit()
	if err != nil {
		t.Fatal(err)
	}
	v, err := r.Call(context.Background(), b.ID, "artifact", Marshal(map[string]any{"path": "first.json", "write": "new"}))
	if err != nil || v.(map[string]any)["deferred"] != true {
		t.Fatal("context cache leaked across activations", v, err)
	}
	complete(t, r, b, "wait")
}

func TestRunwayReflectsRefreshTimeAndFiniteHorizon(t *testing.T) {
	s := fixture(t)
	start := s.Now()
	if err := s.Update("operator", "config", "bounded run", func(st *State) error {
		st.Config.StartsPerHour = 1
		st.Config.FreezeAt = start.Add(20 * time.Minute)
		st.Timers["check"] = Timer{ID: "check", Pursuit: "purpose", Due: start.Add(2 * time.Minute), IntervalSeconds: 120, Active: true, Reason: "review"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	s.Now = func() time.Time { return start.Add(time.Minute) }
	p, err := s.RefreshContext(context.Background(), a.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	// JSON keeps this witness compilable on the pre-change baseline.
	var packet map[string]any
	if err = Decode(Marshal(p), &packet); err != nil {
		t.Fatal(err)
	}
	runway, ok := packet["attention_runway"].(map[string]any)
	if !ok {
		t.Fatal("missing derived attention runway")
	}
	if runway["future_starts_before_refill"] != float64(0) || runway["no_capacity_before_freeze"] != true || runway["timer_firings_before_boundary"] != float64(9) {
		t.Fatal(runway)
	}
	if runway["as_of"] != s.Now().Format(time.RFC3339) {
		t.Fatal("stale resource time", runway)
	}
	complete(t, s, a, "wait")
	r := Open(s.Dir)
	r.Now = s.Now
	if _, _, err = r.Admit(); err == nil {
		t.Fatal("projection minted capacity")
	}
}

func TestRunwayBudgetLoweringAndExpiry(t *testing.T) {
	now := time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC)
	st := NewState("test")
	st.Config.StartsPerHour = 1
	st.Activations["a"] = Activation{Started: now.Add(-50 * time.Minute)}
	st.Activations["b"] = Activation{Started: now.Add(-40 * time.Minute)}
	v := attentionRunway(st, now)
	if !v.Boundary.Equal(now.Add(10*time.Minute)) || !v.BudgetAvailableAt.Equal(now.Add(20*time.Minute)) {
		t.Fatal("first expiry is not necessarily new capacity", v)
	}
	v = attentionRunway(st, now.Add(20*time.Minute))
	if v.FutureStarts != 1 {
		t.Fatal("rolling capacity did not expire", v)
	}
	st.Config.FreezeAt = now.Add(5 * time.Minute)
	if !attentionRunway(st, now).NoCapacityBeforeFreeze {
		t.Fatal("inert future capacity described as available")
	}
}

func TestDeliveredContextCacheIsBoundedAndRevisionSensitive(t *testing.T) {
	s := fixture(t)
	if err := s.Update("operator", "config", "workspace", func(st *State) error { st.Config.Workspace = true; return nil }); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"a", "b", "c", "d", "e", "f", "g", "h", "i", "j"} {
		if _, err := s.Call(context.Background(), a.ID, "artifact", Marshal(map[string]any{"path": path, "write": "{}"})); err != nil {
			t.Fatal(err)
		}
	}
	st, _ := s.Read()
	if len(st.Activations[a.ID].PreparedContexts) != 8 {
		t.Fatal("unbounded context cache")
	}
	norm := itemFixture("new-norm", "New relevant instruction")
	norm.Kind = "norm"
	norm.AppliesTo = []string{"engineering"}
	if err := s.Mutate(a.ID, "new evidence", Batch{Items: []ItemChange{{Item: norm}}}); err != nil {
		t.Fatal(err)
	}
	v, err := s.Call(context.Background(), a.ID, "artifact", Marshal(map[string]any{"path": "j", "write": "{}"}))
	if err != nil || v.(map[string]any)["deferred"] != true {
		t.Fatal("new applicable context bypassed", v, err)
	}
	complete(t, s, a, "wait")
}
