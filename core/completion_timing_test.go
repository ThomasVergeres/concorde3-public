package core

import (
	"context"
	"strings"
	"testing"
	"time"
)

func completionAttention(t *testing.T, value any) Pursuit {
	t.Helper()
	response, ok := value.(map[string]any)
	if !ok || response["completed"] != true {
		t.Fatalf("completion response missing success: %#v", value)
	}
	attention, ok := response["attention"].(Pursuit)
	if !ok {
		t.Fatalf("completion receipt omits committed attention timing: %#v", value)
	}
	return attention
}

func TestCompletionTimingMovingClockMCPRestartLoop(t *testing.T) {
	s := fixture(t)
	now := s.Now()
	start := now
	s.Now = func() time.Time { return now }
	running(t, s)
	a, initial, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if !initial.Pursuit.ReconsiderAt.Equal(start.Add(30 * time.Minute)) {
		t.Fatal("unexpected existing admission anchor", initial.Pursuit)
	}
	// Work and rectification consume real time. Refreshing context must not
	// present the old anchor as the result of the pending wait completion.
	now = start.Add(5 * time.Minute)
	if err = s.BeginRectification(a.ID, "The one-off was delivered; no ongoing service promised", "session"); err != nil {
		t.Fatal(err)
	}
	p, err := s.RefreshContext(context.Background(), a.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	if !p.Pursuit.ReconsiderAt.Equal(initial.Pursuit.ReconsiderAt) {
		t.Fatal("context must describe canonical state, not silently invent a new schedule", p.Pursuit)
	}
	if !strings.Contains(p.Guidance, "current-state eligibility") || !strings.Contains(p.Guidance, "delay at completion") {
		t.Error("pre-completion context leaves the shifting default wait ambiguous", p.Guidance)
	}
	now = start.Add(6 * time.Minute)
	st, _ := s.Read()
	raw, err := s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": Completion{
		ExpectedSeq: st.Seq, Continuation: "wait", Coverage: "No observer; reconsider later without a guaranteed start", Reason: "Adequate manual delivery",
	}}))
	if err != nil {
		t.Fatal(err)
	}
	attention := completionAttention(t, raw)
	due := now.Add(30 * time.Minute)
	if !attention.ReconsiderAt.Equal(due) || attention.Status != "waiting" || !attention.NextAt.IsZero() {
		t.Fatal("response must describe the actual committed undated wait", attention, due)
	}
	if err = s.Finish(a.ID, Outcome{Summary: "Delivery complete; wait committed"}, nil); err != nil {
		t.Fatal(err)
	}
	r := Open(s.Dir)
	r.Now = func() time.Time { return initial.Pursuit.ReconsiderAt }
	if _, _, err = r.Admit(); err == nil {
		t.Fatal("pre-completion forecast became an unintended earlier admission")
	}
	r.Now = func() time.Time { return due.Add(-time.Nanosecond) }
	if _, _, err = r.Admit(); err == nil {
		t.Fatal("early admission after restart")
	}
	r.Now = func() time.Time { return due }
	b, packet, err := r.Admit()
	if err != nil || !strings.Contains(b.Reason, "whole-self reconsideration due") || packet.StartsRemaining != 4 {
		t.Fatal("actual committed timing must retain ordinary admission and budget semantics", b, packet.StartsRemaining, err)
	}
	complete(t, r, b, "stop")
}

func TestCompletionTimingExplicitAndInactiveControls(t *testing.T) {
	for _, continuation := range []string{"wait", "dormant", "stop", "continue"} {
		t.Run(continuation, func(t *testing.T) {
			s := fixture(t)
			now := s.Now()
			start := now
			s.Now = func() time.Time { return now }
			running(t, s)
			a, _, err := s.Admit()
			if err != nil {
				t.Fatal(err)
			}
			now = start.Add(4 * time.Minute)
			if err = s.BeginRectification(a.ID, "Control", "session"); err != nil {
				t.Fatal(err)
			}
			st, _ := s.Read()
			c := Completion{ExpectedSeq: st.Seq, Continuation: continuation, Coverage: "Explicit control", Reason: "No mandatory thought or scheduling method"}
			if continuation == "wait" {
				c.NextAt = start.Add(20 * time.Minute)
			}
			value, err := s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": c}))
			if err != nil {
				t.Fatal(err)
			}
			attention := completionAttention(t, value)
			current, _ := s.Read()
			if attention != current.Portfolio()[a.Pursuit] {
				t.Fatal("receipt does not match committed state", attention, current.Portfolio()[a.Pursuit])
			}
			if continuation == "wait" {
				if !attention.NextAt.Equal(c.NextAt) || !attention.ReconsiderAt.Equal(c.NextAt) {
					t.Fatal("explicit return moved with completion", attention)
				}
			} else if !attention.ReconsiderAt.IsZero() {
				t.Fatal("completion invented a default wait for another choice", attention)
			}
			if err = s.Finish(a.ID, Outcome{Summary: "control complete"}, nil); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestCompletionTimingRejectedRequestReturnsNoCommittedView(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "wait decision pending", "session"); err != nil {
		t.Fatal(err)
	}
	before, _ := s.Read()
	value, err := s.Call(context.Background(), a.ID, "phase_complete", Marshal(map[string]any{"completion": Completion{
		ExpectedSeq: before.Seq - 1, Continuation: "wait", Coverage: "No guarantee", Reason: "Stale decision",
	}}))
	if err == nil {
		t.Fatal("stale sequence accepted")
	}
	if response, ok := value.(map[string]any); ok && response["attention"] != nil {
		t.Fatal("failed transaction returned an uncommitted attention claim", response)
	}
	after, _ := s.Read()
	if string(Marshal(before)) != string(Marshal(after)) {
		t.Fatal("rejected completion changed state")
	}
}
