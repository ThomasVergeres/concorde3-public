package core

import (
	"strings"
	"testing"
	"time"
)

func TestExplicitLongWaitWritebackRestartAndBroadAdmission(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "One method exhausted; purpose remains open", "session"); err != nil {
		t.Fatal(err)
	}
	st, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	requested := s.Now().Add(24 * time.Hour)
	if err = s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Continuation: "wait", NextAt: requested,
		Coverage: "No outside promise", Reason: "Current route cannot yet show advantage"}); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "Local route exhausted"}, nil); err != nil {
		t.Fatal(err)
	}
	st, err = s.Read()
	if err != nil {
		t.Fatal(err)
	}
	if got := st.Items["purpose"].Attention.NextAt; !got.Equal(requested) {
		t.Fatalf("original declared plan lost: %s", got)
	}
	due := st.Portfolio()["purpose"].ReconsiderAt
	if want := s.Now().Add(30 * time.Minute); !due.Equal(want) {
		t.Fatalf("broad return %s, want %s", due, want)
	}
	r := Open(s.Dir)
	r.Now = func() time.Time { return due.Add(-time.Nanosecond) }
	if _, _, err = r.Admit(); err == nil {
		t.Fatal("admitted before protected return")
	}
	r.Now = func() time.Time { return due }
	b, p, err := r.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if b.Pursuit != "purpose" || !strings.Contains(b.Reason, "whole-self reconsideration due") {
		t.Fatalf("return lacked broad provenance: %+v", b)
	}
	if p.StartsRemaining != 4 {
		t.Fatalf("return minted capacity: %d", p.StartsRemaining)
	}
	complete(t, r, b, "stop")
}

func TestExplicitLongWaitDoesNotSuppressProtectedBroadReturn(t *testing.T) {
	st := NewState("An open continuing undertaking")
	now := time.Now().UTC()
	i := st.Items["purpose"]
	i.Attention.EffortState = "waiting"
	i.Attention.DeferredAt = now.Add(-2 * time.Hour)
	i.Attention.NextAt = now.Add(24 * time.Hour)
	st.Items[i.ID] = i
	if got := st.Portfolio()["purpose"].ReconsiderAt; got.After(now) || got.IsZero() {
		t.Fatalf("long explicit wait erased protected broad opportunity: %s", got)
	}
	if ready, _ := ready(st, st.Portfolio()["purpose"], now); !ready {
		t.Fatal("open whole-self intention was not eligible for reconsideration")
	}
	// The cap belongs to the configured whole-self intention, not every local
	// commitment: a local, intentionally dated return is still respected.
	st.Items["purpose"] = func() Item {
		item := st.Items["purpose"]
		item.Attention.Weight = .2
		return item
	}()
	st.Items["local"] = Item{ID: "local", Node: "undertaking", Kind: "intention", Status: "active",
		Attention: &Attention{Weight: .8, EffortState: "waiting", DeferredAt: now.Add(-2 * time.Hour), NextAt: now.Add(24 * time.Hour)}}
	if got := st.Portfolio()["local"].ReconsiderAt; !got.Equal(now.Add(24 * time.Hour)) {
		t.Fatalf("local explicit return changed: %s", got)
	}
	for _, state := range []string{"dormant", "stopped"} {
		item := st.Items["purpose"]
		item.Attention.EffortState = state
		item.Attention.NextAt = time.Time{}
		st.Items[item.ID] = item
		if got := st.Portfolio()["purpose"].ReconsiderAt; !got.IsZero() {
			t.Fatalf("deliberate %s was reanimated: %s", state, got)
		}
	}
}
