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

func TestDeferralMCPWritebackRestartAndReentry(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Unfinished commitment remains", "session"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	_, err = s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": Completion{ExpectedSeq: st.Seq, Continuation: "wait", Coverage: "uncovered", Reason: "defer"}}))
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "deferred"}, nil); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	due := st.Portfolio()["purpose"].ReconsiderAt
	if !due.Equal(s.Now().Add(30 * time.Minute)) {
		t.Fatal(due)
	}
	r := Open(s.Dir)
	r.Now = func() time.Time { return due.Add(-time.Nanosecond) }
	if _, _, err = r.Admit(); err == nil {
		t.Fatal("early return")
	}
	r.Now = func() time.Time { return due }
	b, p, err := r.Admit()
	if err != nil || !strings.Contains(b.Reason, "whole-self reconsideration due") {
		t.Fatal(b, err)
	}
	if p.StartsRemaining != 4 {
		t.Fatal("extra budget minted", p.StartsRemaining)
	}
	complete(t, r, b, "stop")
	r.Now = func() time.Time { return due.Add(24 * time.Hour) }
	if _, _, err = r.Admit(); err == nil {
		t.Fatal("stopped undertaking reanimated")
	}
}

func TestContinuityEligibilityControls(t *testing.T) {
	now := time.Now().UTC()
	for _, tc := range []struct {
		name, state, status string
		weight              float64
		next                time.Time
		event               bool
		want                bool
	}{
		{"local expired", "waiting", "active", .8, time.Time{}, false, true},
		{"explicit future respected", "waiting", "active", .8, now.Add(time.Hour), false, false},
		{"explicit due", "waiting", "active", .8, now.Add(-time.Second), false, true},
		{"dormant quiet", "dormant", "active", .8, time.Time{}, false, false},
		{"dormant observation", "dormant", "active", .8, time.Time{}, true, true},
		{"stopped observation", "stopped", "active", .8, time.Time{}, true, false},
		{"attained", "waiting", "attained", .8, time.Time{}, true, false},
		{"abandoned", "ready", "abandoned", .8, time.Time{}, true, false},
		{"zero allocation", "waiting", "active", 0, time.Time{}, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			st := NewState("test")
			fund(&st, "local", tc.weight, tc.state)
			i := st.Items["local"]
			i.Status = tc.status
			i.Attention.DeferredAt = now.Add(-10 * time.Minute)
			i.Attention.NextAt = tc.next
			st.Items[i.ID] = i
			if tc.event {
				st.Wakes["news"] = Wake{ID: "news", Pursuit: "local", At: now}
			}
			got, _ := ready(st, st.Portfolio()["local"], now)
			if got != tc.want {
				t.Fatalf("ready=%v want=%v", got, tc.want)
			}
		})
	}
}

func TestDeferralClockNotRenewedByMemoryOrWeightEdit(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	complete(t, s, a, "wait")
	st, _ := s.Read()
	before := st.Portfolio()["purpose"].ReconsiderAt
	now := s.Now().Add(20 * time.Minute)
	s.Now = func() time.Time { return now }
	i := st.Items["purpose"]
	i.Text += " Updated evidence."
	i.Attention.Weight = .9
	i.Attention.DeferredAt = now.Add(time.Hour)
	if err := s.Mutate("operator", "revise evidence, not timing", Batch{ExpectedSeq: &st.Seq, Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}}); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if !st.Portfolio()["purpose"].ReconsiderAt.Equal(before) {
		t.Fatal("text edit or forged anchor postponed return")
	}
}

func TestDeferredFairnessUnderFloodAndFreeze(t *testing.T) {
	st := NewState("whole undertaking")
	now := time.Now().UTC()
	fund(&st, "purpose", .2, "waiting")
	fund(&st, "busy", .7, "ready")
	fund(&st, "neglected", .1, "waiting")
	for id, i := range st.Items {
		if i.Attention != nil {
			i.Attention.DeferredAt = now.Add(-time.Hour)
			st.Items[id] = i
		}
	}
	// More ordinary memory must not mint attention. Every candidate competes
	// through the same bounded-urgency/weighted-fair selection path.
	for j := 0; j < 1000; j++ {
		st.Wakes[fmt.Sprint(j)] = Wake{Pursuit: "busy", At: now}
	}
	seen := map[string]int{}
	for j := 0; j < 30; j++ {
		p, _, err := selectPursuit(&st, now)
		if err != nil {
			t.Fatal(err)
		}
		seen[p.ID]++
		st.Starts++
	}
	if seen["purpose"] == 0 || seen["neglected"] == 0 {
		t.Fatal("starved", seen)
	}
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	complete(t, s, a, "wait")
	cutoff := s.Now().Add(time.Minute)
	if err := s.Update("operator", "config", "freeze before automatic due", func(st *State) error { st.Config.FreezeAt = cutoff; return nil }); err != nil {
		t.Fatal(err)
	}
	s.Now = func() time.Time { return cutoff.Add(time.Hour) }
	if _, _, err := s.Admit(); err == nil {
		t.Fatal("automatic return bypassed terminal freeze")
	}
}

func TestDormancyTimerRecoveryAndLegacyRead(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	complete(t, s, a, "dormant")
	st, _ := s.Read()
	if !st.Portfolio()["purpose"].ReconsiderAt.IsZero() {
		t.Fatal("dormancy has default wake")
	}
	if err := s.Mutate("operator", "owned observer clock", Batch{Timers: []Timer{{ID: "check", Pursuit: "purpose", Due: s.Now(), Active: true, Reason: "observer event"}}}); err != nil {
		t.Fatal(err)
	}
	b, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	// Pending integration still gets recovered even after ending ordinary effort.
	if err = s.BeginRectification(b.ID, "must preserve evidence", ""); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(b.ID, Outcome{}, fmt.Errorf("interrupted")); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	i := st.Items["purpose"]
	i.Status = "attained"
	i.Attention.EffortState = "stopped"
	if err = s.Mutate("operator", "target attained while recovery pending", Batch{ExpectedSeq: &st.Seq, Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}}); err != nil {
		t.Fatal(err)
	}
	now := s.Now().Add(2 * time.Minute)
	s.Now = func() time.Time { return now }
	c, _, err := s.Admit()
	if err != nil || c.Phase != "rectification" {
		t.Fatal("lost recovery", err)
	}
	complete(t, s, c, "stop")
	st = NewState("old schema-v2 state")
	i = st.Items["purpose"]
	i.Attention.EffortState = "waiting"
	i.Attention.DeferredAt = time.Time{}
	i.UpdatedAt = now.Add(-time.Hour)
	st.Items[i.ID] = i
	got, _ := ready(st, st.Portfolio()["purpose"], now)
	if !got {
		t.Fatal("legacy undated wait stays abandoned")
	}
	if err = Validate(st); err != nil {
		t.Fatal(err)
	}
}

func TestActualDeferredCommandLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "real subprocess continuity", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestContinuityCommandHelper", "continuity-fixture"}
		st.Config.ReconsiderSeconds = 1
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Run(context.Background(), true, nil); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	due := st.Portfolio()["purpose"].ReconsiderAt
	r := Open(s.Dir)
	r.Now = func() time.Time { return due }
	if err := r.Run(context.Background(), true, nil); err != nil {
		t.Fatal(err)
	}
	st, _ = r.Read()
	if st.Items["progress"].Revision != 2 || len(st.Activations) != 2 {
		t.Fatal("fresh activation did not use durable progress")
	}
	for _, a := range st.Activations {
		if a.Status != "completed" || a.Completion == nil {
			t.Fatal(a)
		}
	}
}

func TestContinuityCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "continuity-fixture") {
		return
	}
	if os.Args[len(os.Args)-1] == "--concorde-capabilities" {
		fmt.Print(`{"protocol":2,"continuation":true}`)
		os.Exit(0)
	}
	var p Packet
	_ = json.NewDecoder(os.Stdin).Decode(&p)
	st, _ := Open(os.Getenv("CONCORDE3_INSTANCE")).Read()
	if p.Phase == "work" {
		i := itemFixture("progress", "Useful durable working evidence")
		old := st.Items[i.ID]
		_ = json.NewEncoder(os.Stdout).Encode(Outcome{Summary: "continued work", Changes: Batch{Items: []ItemChange{{ExpectedRevision: old.Revision, Item: i}}}})
	} else {
		_ = json.NewEncoder(os.Stdout).Encode(Outcome{Summary: "rectified", Completion: &Completion{ExpectedSeq: st.Seq, Continuation: "wait", Coverage: "uncovered; default reconsideration only", Reason: "unfinished but deferred"}})
	}
	os.Exit(0)
}

func TestLegacyStoppedScheduleRemainsReadableButCannotBeCreated(t *testing.T) {
	// Old v2 accepted stop+next_at and ignored its clock. Read that state without
	// reanimation, but reject new contradictory scheduling requests.
	st := NewState("old completed undertaking")
	i := st.Items["purpose"]
	i.Attention.EffortState = "stopped"
	i.Attention.NextAt = time.Now().Add(-time.Hour)
	st.Items[i.ID] = i
	if err := Validate(st); err != nil {
		t.Fatal("old valid state no longer readable", err)
	}
	if got, _ := ready(st, st.Portfolio()["purpose"], time.Now()); got {
		t.Fatal("legacy stopped clock revived work")
	}
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	if err := s.BeginRectification(a.ID, "done", ""); err != nil {
		t.Fatal(err)
	}
	state, _ := s.Read()
	for _, continuation := range []string{"stop", "dormant"} {
		err := s.CompletePhase(a.ID, Completion{ExpectedSeq: state.Seq, Continuation: continuation, NextAt: s.Now().Add(time.Minute), Coverage: "none", Reason: "contradictory request"})
		if err == nil {
			t.Fatal("accepted contradictory schedule", continuation)
		}
	}
}

func TestLegacyClockSurvivesFirstMemoryEdit(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	complete(t, s, a, "wait")
	if err := s.Update("operator", "fixture", "old state with a newer text timestamp", func(st *State) error {
		i := st.Items["purpose"]
		i.Attention.DeferredAt = time.Time{}
		i.UpdatedAt = s.Now().Add(20 * time.Minute)
		st.Items[i.ID] = i
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	before := st.Portfolio()["purpose"].ReconsiderAt
	i := st.Items["purpose"]
	i.Text += " More information."
	if err := s.Mutate("operator", "edit memory only", Batch{ExpectedSeq: &st.Seq, Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}}); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if !st.Portfolio()["purpose"].ReconsiderAt.Equal(before) {
		t.Fatal("first legacy text edit moved return clock")
	}
}
