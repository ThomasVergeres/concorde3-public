package core

import (
	"context"
	"strings"
	"testing"
	"time"
)

// Use decoded JSON so this regression runs on the unchanged implementation.
func requireInstanceFreezeScope(t *testing.T, p Packet, cutoff time.Time) {
	t.Helper()
	var raw map[string]any
	if err := Decode(Marshal(p), &raw); err != nil {
		t.Fatal(err)
	}
	scope, ok := raw["freeze_scope"].(string)
	if !ok || !strings.Contains(scope, "this instance") || !strings.Contains(scope, "independent external services") {
		t.Fatal("finite execution boundary lacks its instance-local scope", raw["freeze_scope"])
	}
	if !p.FreezeAt.Equal(cutoff) {
		t.Fatal("scope description changed the configured boundary")
	}
}

func TestInstanceFreezeScopeThroughWritebackAndRestart(t *testing.T) {
	s := fixture(t)
	cutoff := s.Now().Add(20 * time.Minute)
	if err := s.Update("operator", "config", "finite local execution", func(st *State) error {
		st.Config.FreezeAt = cutoff
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	requireInstanceFreezeScope(t, p, cutoff)
	if err = s.Mutate(a.ID, "independent source, not runtime inference", Batch{Items: []ItemChange{{Item: itemFixture("provider", "The independent provider has its own operating terms; current availability is source evidence.")}}}); err != nil {
		t.Fatal(err)
	}
	for _, phase := range []string{"work", "rectification"} {
		if phase == "rectification" {
			if err = s.BeginRectification(a.ID, "retain actual provider evidence", "session"); err != nil {
				t.Fatal(err)
			}
		}
		p, err = s.RefreshContext(context.Background(), a.ID, "")
		if err != nil {
			t.Fatal(err)
		}
		requireInstanceFreezeScope(t, p, cutoff)
		v, err := s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{}))
		if err != nil {
			t.Fatal(err)
		}
		var viaTool Packet
		if err = Decode(Marshal(v), &viaTool); err != nil {
			t.Fatal(err)
		}
		requireInstanceFreezeScope(t, viaTool, cutoff)
	}
	complete(t, s, a, "continue")
	r := Open(s.Dir)
	r.Now = s.Now
	b, p, err := r.Admit()
	if err != nil {
		t.Fatal(err)
	}
	requireInstanceFreezeScope(t, p, cutoff)
	st, err := r.Read()
	if err != nil || st.Items["provider"].Actor != a.ID || len(st.Activations) != 2 {
		t.Fatal("description changed continuity or admission accounting", err)
	}
	complete(t, r, b, "wait")
	r.Now = func() time.Time { return cutoff }
	if _, _, err = r.Admit(); err == nil {
		t.Fatal("scope clarification extended execution")
	}
}

func TestNoFreezeDoesNotInventScopeOrProviderCoverage(t *testing.T) {
	s := fixture(t)
	running(t, s)
	_, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err = Decode(Marshal(p), &raw); err != nil {
		t.Fatal(err)
	}
	if _, exists := raw["freeze_scope"]; exists {
		t.Fatal("unconfigured freeze acquired a scope or external-service verdict")
	}
}
