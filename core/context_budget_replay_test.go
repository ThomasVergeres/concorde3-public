package core

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Optional read-only qualification of a private incident. It does not mutate the
// captured self or demonstrate that the model will use the retrieved evidence.
func TestPrivateContextBudgetQualification(t *testing.T) {
	dir := os.Getenv("C3_CONTEXT_BUDGET_REPLAY")
	if dir == "" {
		t.Skip("private pre-admission snapshot not supplied")
	}
	var st State
	var original Packet
	for name, target := range map[string]any{"pre-admission-state.json": &st, "original-work-context.json": &original} {
		b, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		if err = json.Unmarshal(b, target); err != nil {
			t.Fatal(err)
		}
	}
	a := Activation{ID: original.Activation, Pursuit: original.Pursuit.ID,
		Phase: "work", Status: "running", Started: original.At,
		Deadline: original.Deadline, Config: st.Config}
	if _, exists := st.Activations[a.ID]; exists {
		t.Fatal("not pre-admission")
	}
	st.Activations[a.ID] = a
	for _, phase := range []string{"work", "rectification"} {
		a.Phase = phase
		small := clone(st)
		small.Config.ContextBytes = min(st.Config.ContextBytes, st.Config.ResponseBytes-1024)
		p := assembleAt(small, a, original.At)
		t.Logf("%s original tool allowance %d: %q", phase, small.Config.ContextBytes, p.Error)
		large := clone(st)
		large.Config.ResponseBytes = st.Config.ContextBytes + 1024
		q := assembleAt(large, a, original.At)
		if q.Error != "" || len(Marshal(q)) > large.Config.ContextBytes {
			t.Fatalf("full configured context not usable: %s", q.Error)
		}
		seen := map[string]bool{}
		for _, i := range q.Items {
			seen[i.Item.ID] = true
		}
		for _, id := range st.Config.Global {
			if !seen[id] {
				t.Fatalf("lost required binding %s", id)
			}
		}
		t.Logf("%s response allowance %d preserves full bounded context: %d bytes", phase, large.Config.ResponseBytes, len(Marshal(q)))
		for _, responseBytes := range []int{st.Config.ResponseBytes, st.Config.ContextBytes + 1024} {
			s := fixture(t)
			s.Now = func() time.Time { return original.At }
			copyState := clone(st)
			copyState.Config.ResponseBytes = responseBytes
			copyActivation := a
			copyActivation.Config = copyState.Config
			copyState.Activations[a.ID] = copyActivation
			if err := s.Update("operator", "fixture", "Isolated read-only tool qualification", func(target *State) error {
				*target = copyState
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			v, err := s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{"query": "Review current support situation"}))
			if responseBytes == st.Config.ResponseBytes {
				if err == nil {
					t.Fatal("expected captured narrow-budget failure")
				}
				t.Logf("%s actual original context tool: %v", phase, err)
			} else {
				if err != nil {
					t.Fatal(err)
				}
				if len(Marshal(v)) > responseBytes {
					t.Fatal("tool response exceeds bound")
				}
				t.Logf("%s actual candidate context tool: %d bytes", phase, len(Marshal(v)))
			}
		}
	}
}
