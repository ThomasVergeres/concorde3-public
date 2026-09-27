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

func inflightObservation(t *testing.T) (*Store, Activation) {
	t.Helper()
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Notify("purpose", "inflight", "Current source result is now available"); err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "Inspected the current source and delivered result", "session"); err != nil {
		t.Fatal(err)
	}
	value, err := s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{"query": "current receiving result"}))
	if err != nil || len(value.(Packet).Wakes) != 1 {
		t.Fatal("in-flight observation not exposed", err)
	}
	return s, a
}

func TestObservationAcknowledgmentRejectsUnsafeBatchesAtomically(t *testing.T) {
	for _, variant := range []string{"missing_seq", "stale", "unknown", "duplicate", "future", "empty"} {
		t.Run(variant, func(t *testing.T) {
			s, a := inflightObservation(t)
			if variant == "future" {
				if err := s.Update("operator", "fixture", "Future observation", func(st *State) error {
					w := st.Wakes["inflight"]
					w.At = s.Now().Add(time.Hour)
					st.Wakes[w.ID] = w
					return nil
				}); err != nil {
					t.Fatal(err)
				}
			}
			before, _ := s.Read()
			seq := before.Seq
			ids := []string{"inflight"}
			if variant == "stale" {
				seq--
			}
			if variant == "unknown" {
				ids = append(ids, "absent")
			}
			if variant == "duplicate" {
				ids = append(ids, "inflight")
			}
			if variant == "empty" {
				ids = nil
			}
			q := map[string]any{"expected_seq": seq, "ids": ids, "reason": "Reviewed exact observation"}
			if variant == "missing_seq" {
				delete(q, "expected_seq")
			}
			if _, err := s.Call(context.Background(), a.ID, "acknowledge_observations", Marshal(q)); err == nil {
				t.Fatal("invalid acknowledgment accepted")
			}
			after, _ := s.Read()
			if Digest(Marshal(before)) != Digest(Marshal(after)) {
				t.Fatal("rejected acknowledgment changed state")
			}
		})
	}
	s, a := inflightObservation(t)
	before, _ := s.Read()
	bad := Batch{ExpectedSeq: &before.Seq, AcknowledgeObservations: []string{"inflight"}, Items: []ItemChange{{ExpectedRevision: 99, Item: itemFixture("new", "Invalid revision")}}}
	if err := s.Mutate(a.ID, "Compound acknowledgment and knowledge update", bad); err == nil {
		t.Fatal("invalid compound update accepted")
	}
	after, _ := s.Read()
	if Digest(Marshal(before)) != Digest(Marshal(after)) {
		t.Fatal("failed compound transaction consumed observation")
	}
}

func TestObservationAcknowledgmentCannotEraseConcurrentEventOrAttentionChoice(t *testing.T) {
	s, a := inflightObservation(t)
	old, _ := s.Read()
	if err := s.Notify("purpose", "concurrent", "Arrived after the reviewed sequence"); err != nil {
		t.Fatal(err)
	}
	before, _ := s.Read()
	if err := s.Mutate(a.ID, "Outdated acknowledgment", Batch{ExpectedSeq: &old.Seq, AcknowledgeObservations: []string{"inflight"}}); err == nil {
		t.Fatal("stale acknowledgment accepted")
	}
	after, _ := s.Read()
	if Digest(Marshal(before)) != Digest(Marshal(after)) {
		t.Fatal("concurrent evidence lost")
	}
	if err := s.Mutate(a.ID, "Both exact observations now accounted for", Batch{ExpectedSeq: &after.Seq, AcknowledgeObservations: []string{"inflight", "concurrent"}}); err != nil {
		t.Fatal(err)
	}
	after, _ = s.Read()
	if Digest(Marshal(before.Items)) != Digest(Marshal(after.Items)) || before.Starts != after.Starts {
		t.Fatal("acknowledgment changed attention or budget")
	}
	complete(t, s, a, "continue")
	if _, _, err := s.Admit(); err != nil {
		t.Fatal("explicit continuation was inhibited", err)
	}
}

func TestObservationAcknowledgmentHasWholeSelfAuthorityAndPreservesTimers(t *testing.T) {
	s, a := inflightObservation(t)
	if err := s.Update("operator", "fixture", "Another domain and due timer", func(st *State) error {
		i := st.Items["purpose"]
		i.Attention.Weight = .8
		st.Items[i.ID] = i
		fund(st, "other", .2, "dormant")
		st.Timers["return"] = Timer{ID: "return", Pursuit: "purpose", Due: s.Now(), Active: true, Reason: "Distinct timed obligation"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Notify("other", "remote", "A source relevant to another domain"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	if err := s.Mutate(a.ID, "Inspected local and distant observations", Batch{ExpectedSeq: &st.Seq, AcknowledgeObservations: []string{"inflight", "remote"}}); err != nil {
		t.Fatal(err)
	}
	complete(t, s, a, "wait")
	_, _, err := s.Admit()
	if err != nil {
		t.Fatal("acknowledgment removed unrelated due timer", err)
	}
	st, _ = s.Read()
	if st.Wakes["remote"].ConsumedBy != a.ID {
		t.Fatal("distant observation acknowledgment lost")
	}
}

func TestObservationAcknowledgmentPreservesOtherConsumersAndUnhandledEvidence(t *testing.T) {
	s, a := inflightObservation(t)
	if err := s.Notify("purpose", "unhandled", "A distinct unresolved update"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	if err := s.Mutate("operator", "Explicit operator review", Batch{ExpectedSeq: &st.Seq, AcknowledgeObservations: []string{"inflight"}}); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if err := s.Mutate(a.ID, "Already handled observation inspected again", Batch{ExpectedSeq: &st.Seq, AcknowledgeObservations: []string{"inflight"}}); err != nil {
		t.Fatal(err)
	}
	complete(t, s, a, "wait")
	_, p, err := s.Admit()
	if err != nil || len(p.Wakes) != 1 || p.Wakes[0].ID != "unhandled" {
		t.Fatal("unacknowledged evidence was suppressed", err)
	}
	st, _ = s.Read()
	if st.Wakes["inflight"].ConsumedBy != "operator" {
		t.Fatal("original consumer reassigned")
	}
}

func TestObservationAcknowledgmentDoesNotCancelInterruptedRectification(t *testing.T) {
	s, a := inflightObservation(t)
	st, _ := s.Read()
	if err := s.Mutate(a.ID, "Current observation accounted for", Batch{ExpectedSeq: &st.Seq, AcknowledgeObservations: []string{"inflight"}}); err != nil {
		t.Fatal(err)
	}
	if err := s.Finish(a.ID, Outcome{Summary: "Rectification interrupted after acknowledgment"}, fmt.Errorf("disposable interruption")); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	reopened := Open(s.Dir)
	reopened.Now = func() time.Time { return st.Activations[a.ID].RetryAt.Add(time.Second) }
	next, p, err := reopened.Admit()
	if err != nil || next.RecoveryOf != a.ID || next.Phase != "rectification" {
		t.Fatal("acknowledgment erased unfinished rectification", err)
	}
	if len(p.Wakes) != 1 || p.Wakes[0].ID != "inflight" {
		t.Fatal("acknowledged evidence lost to its recovery lineage")
	}
}

func TestObservationAcknowledgmentActualCommandLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Real command harness qualification", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestObservationAckCommandHelper", "ack-loop"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Run(context.Background(), true, nil); err != nil {
		t.Fatal(err)
	}
	reopened := Open(s.Dir)
	st, err := reopened.Read()
	if err != nil || len(st.Activations) != 1 {
		t.Fatal("unexpected activation history", err)
	}
	for _, a := range st.Activations {
		if a.Status != "completed" || st.Wakes["during-work"].ConsumedBy != a.ID {
			t.Fatal("command did not rectify/acknowledge", a.Summary)
		}
	}
	running(t, reopened)
	if _, _, err := reopened.Admit(); err == nil {
		t.Fatal("completed command's handled observation forced a restart")
	}
	if err := reopened.Notify("purpose", "later", "New circumstances after completed work"); err != nil {
		t.Fatal(err)
	}
	_, p, err := reopened.Admit()
	if err != nil || len(p.Wakes) != 1 || p.Wakes[0].ID != "later" {
		t.Fatal("fresh observation did not reach next embodiment", err)
	}
}

func TestObservationAckCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "ack-loop") {
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
	s := Open(os.Getenv("CONCORDE3_INSTANCE"))
	out := Outcome{Summary: "Accounted for current source"}
	if p.Phase == "work" {
		if err := s.Notify("purpose", "during-work", "Source arrived and was handled during this work"); err != nil {
			panic(err)
		}
	} else {
		if len(p.Wakes) != 1 || p.Wakes[0].ID != "during-work" {
			panic("new observation missing in rectification")
		}
		st, _ := s.Read()
		if _, err := s.Call(context.Background(), p.Activation, "acknowledge_observations", Marshal(map[string]any{"expected_seq": st.Seq, "ids": []string{"during-work"}, "reason": "Current source accounted for; no extra wake needed"})); err != nil {
			panic(err)
		}
		st, _ = s.Read()
		out.Completion = &Completion{ExpectedSeq: st.Seq, Continuation: "wait", Coverage: "Current source accounted for; future events remain eligible", Reason: "Legitimate rest"}
	}
	_ = json.NewEncoder(os.Stdout).Encode(out)
	os.Exit(0)
}

func TestConsideringInflightObservationDoesNotSilentlyDismissIt(t *testing.T) {
	s, a := inflightObservation(t)
	st, _ := s.Read()
	if err := s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Considered: []string{"inflight"}, Continuation: "wait", Coverage: "Inspected the result", Reason: "No further work requested"}); err != nil {
		t.Fatal(err)
	}
	if err := s.Finish(a.ID, Outcome{Summary: "Handled current result"}, nil); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.Admit(); err != nil {
		t.Fatal("legacy pending observation was silently dropped", err)
	}
}

func TestExplicitObservationAcknowledgmentPermitsRestAndFreshWakeAfterRestart(t *testing.T) {
	s, a := inflightObservation(t)
	before, _ := s.Read()
	_, err := s.Call(context.Background(), a.ID, "acknowledge_observations", Marshal(map[string]any{
		"expected_seq": before.Seq, "ids": []string{"inflight"}, "reason": "Current result accounted for; no extra start needed for this observation"}))
	if err != nil {
		t.Fatal(err)
	}
	complete(t, s, a, "wait")
	reopened := Open(s.Dir)
	reopened.Now = s.Now
	after, err := reopened.Read()
	if err != nil {
		t.Fatal(err)
	}
	w := after.Wakes["inflight"]
	if w.ConsumedBy != a.ID || w.Evidence != before.Wakes[w.ID].Evidence || !w.At.Equal(before.Wakes[w.ID].At) {
		t.Fatal("acknowledgment lost evidence or attribution")
	}
	if _, _, err := reopened.Admit(); err == nil {
		t.Fatal("acknowledged observation forced another start")
	}
	if err := reopened.Notify("purpose", "inflight", w.Evidence); err != nil {
		t.Fatal(err)
	}
	if _, _, err := reopened.Admit(); err == nil {
		t.Fatal("idempotent notification resurrected acknowledged work")
	}
	if err := reopened.Notify("purpose", "fresh", "A distinct new source requires attention"); err != nil {
		t.Fatal(err)
	}
	next, packet, err := reopened.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if next.ID == a.ID || len(packet.Wakes) != 1 || packet.Wakes[0].ID != "fresh" {
		t.Fatal("new evidence lost or old evidence resurrected")
	}
}
