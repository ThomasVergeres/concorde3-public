package core

import (
	"context"
	"testing"
	"time"
)

func TestRequestedWakeMustPrecedeTerminalFreeze(t *testing.T) {
	for _, path := range []string{"completion", "timer", "attention"} {
		for _, delta := range []time.Duration{0, time.Second} {
			t.Run(path+delta.String(), func(t *testing.T) {
				s := fixture(t)
				cutoff := s.Now().Add(5 * time.Minute)
				if err := s.Update("operator", "config", "finite episode", func(st *State) error { st.Config.FreezeAt = cutoff; return nil }); err != nil {
					t.Fatal(err)
				}
				running(t, s)
				a, _, err := s.Admit()
				if err != nil {
					t.Fatal(err)
				}
				if err = s.BeginRectification(a.ID, "Current work completed; maintaining responsibility remains", "session"); err != nil {
					t.Fatal(err)
				}
				before, _ := s.Read()
				at := cutoff.Add(delta)
				switch path {
				case "completion":
					_, err = s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": Completion{ExpectedSeq: before.Seq, Continuation: "wait", NextAt: at, Coverage: "proposed future attention", Reason: "reconsider"}}))
				case "timer":
					err = s.Mutate(a.ID, "schedule future check", Batch{Timers: []Timer{{ID: "return", Pursuit: "purpose", Due: at, Active: true, Reason: "reconsider"}}})
				case "attention":
					i := before.Items["purpose"]
					i.Attention.NextAt = at
					err = s.Mutate(a.ID, "schedule through intention", Batch{ExpectedSeq: &before.Seq, Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}})
				}
				if err == nil {
					t.Fatal("accepted attention request at/after terminal freeze")
				}
				after, _ := s.Read()
				if after.Seq != before.Seq {
					t.Fatal("rejected request changed durable state")
				}
				// Explicitly uncovered rest is still legal. No forced activity/coverage.
				complete(t, s, a, "wait")
			})
		}
	}
}

func TestHorizonCorrectionMCPWritebackRestartAndAdmission(t *testing.T) {
	s := fixture(t)
	cutoff := s.Now().Add(5 * time.Minute)
	if err := s.Update("operator", "config", "finite trial", func(st *State) error { st.Config.FreezeAt = cutoff; return nil }); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Work completed", "session"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	completion := Completion{ExpectedSeq: st.Seq, Continuation: "wait", NextAt: cutoff, Coverage: "planned recheck", Reason: "continue maintenance"}
	if _, err = s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": completion})); err == nil {
		t.Fatal("accepted impossible wake")
	}
	completion.NextAt = s.Now().Add(time.Minute)
	if _, err = s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": completion})); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "scheduled feasible return"}, nil); err != nil {
		t.Fatal(err)
	}
	r := Open(s.Dir)
	r.Now = func() time.Time { return completion.NextAt }
	b, _, err := r.Admit()
	if err != nil || b.Phase != "work" {
		t.Fatal("feasible return lost across restart", err)
	}
	complete(t, r, b, "wait")
}

func TestNoTerminalFreezeDoesNotLimitLongRangePlans(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Work completed", "session"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	err = s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Continuation: "wait", NextAt: s.Now().AddDate(1, 0, 0), Coverage: "annual undertaking", Reason: "genuine long horizon"})
	if err != nil {
		t.Fatal(err)
	}
}
