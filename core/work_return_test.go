package core

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"testing"
	"time"
)

func TestWorkReturnIsExplicitlyDisabledByDefault(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "A new implication may need work", ""); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	_, err = s.Call(context.Background(), a.ID, "resume_work", Marshal(map[string]any{"expected_seq": st.Seq, "reason": "Act on newly discovered evidence"}))
	if err == nil || !strings.Contains(err.Error(), "disabled") {
		t.Fatalf("missing explicit optional work-return contract: %v", err)
	}
	after, _ := s.Read()
	if after.Seq != st.Seq {
		t.Fatal("rejected request changed state")
	}
}

func returnFixture(t *testing.T) (*Store, Activation) {
	s := fixture(t)
	if err := s.Update("operator", "config", "Enable bounded experiment", func(st *State) error { st.Config.WorkReentry = true; return nil }); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "New evidence needs actual work", ""); err != nil {
		t.Fatal(err)
	}
	return s, a
}

func TestWorkReturnBoundsAndStateOnlyRequest(t *testing.T) {
	s, a := returnFixture(t)
	st, _ := s.Read()
	if err := s.RequestWorkReturn(a.ID, st.Seq-1, "Act"); err == nil {
		t.Fatal("stale request accepted")
	}
	if err := s.RequestWorkReturn(a.ID, st.Seq, ""); err == nil {
		t.Fatal("empty reason accepted")
	}
	if err := s.RequestWorkReturn(a.ID, st.Seq, "New source arrived"); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if st.Activations[a.ID].Phase != "rectification" {
		t.Fatal("request permitted work inside rectification turn")
	}
	if _, err := s.Call(context.Background(), a.ID, "artifact", Marshal(map[string]any{"path": "product.txt", "write": "not yet"})); err == nil || !strings.Contains(err.Error(), "state-only") {
		t.Fatal(err)
	}
	if err := s.RequestWorkReturn(a.ID, st.Seq, "Duplicate"); err == nil {
		t.Fatal("duplicate request")
	}
	if err := s.beginReturnedWork(a.ID, "same-session"); err != nil {
		t.Fatal(err)
	}
	st, _ = Open(s.Dir).Read()
	x := st.Activations[a.ID]
	if x.WorkReturns != 1 || x.Session != "same-session" || !x.Started.Equal(a.Started) || !x.Deadline.Equal(a.Deadline) || st.Starts != 1 {
		t.Fatal("return changed lease, budget or provenance", x)
	}
	if err := s.BeginRectification(a.ID, "Returned work completed", x.Session); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if err := s.RequestWorkReturn(a.ID, st.Seq, "Another return"); err == nil {
		t.Fatal("unbounded phase loop")
	}
	if err := s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Continuation: "dormant", Coverage: "No remaining duty", Reason: "Complete"}); err != nil {
		t.Fatal(err)
	}
	if err := s.Finish(a.ID, Outcome{Summary: "Completed bounded loop"}, nil); err != nil {
		t.Fatal(err)
	}
}

func TestWorkReturnLateRecoveryCancellationAndFreeze(t *testing.T) {
	for _, mode := range []string{"late", "recovery", "freeze", "completion", "late-after-request"} {
		t.Run(mode, func(t *testing.T) {
			s, a := returnFixture(t)
			st, _ := s.Read()
			if mode == "completion" || mode == "late-after-request" {
				if err := s.RequestWorkReturn(a.ID, st.Seq, "Potential action"); err != nil {
					t.Fatal(err)
				}
				st, _ = s.Read()
			}
			switch mode {
			case "late", "late-after-request":
				s.Now = func() time.Time { return workBoundary(a) }
			case "recovery":
				if err := s.Finish(a.ID, Outcome{}, fmt.Errorf("interrupted before further work")); err != nil {
					t.Fatal(err)
				}
				next := a.Started.Add(2 * time.Minute)
				s.Now = func() time.Time { return next }
				var err error
				a, _, err = s.Admit()
				if err != nil {
					t.Fatal(err)
				}
				st, _ = s.Read()
				if a.RecoveryOf == "" {
					t.Fatal("not recovery")
				}
			case "freeze":
				if err := s.Update("operator", "freeze", "test closure", func(st *State) error { st.Mode = "frozen"; return nil }); err != nil {
					t.Fatal(err)
				}
				st, _ = s.Read()
			case "completion":
				if err := s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Continuation: "dormant", Coverage: "Deliberate rest", Reason: "Supersede work request"}); err != nil {
					t.Fatal(err)
				}
				st, _ = s.Read()
				if st.Activations[a.ID].WorkReturnReason != "" {
					t.Fatal("completion failed to supersede request")
				}
			}
			before, _ := s.Read()
			var err error
			if mode == "late-after-request" {
				err = s.beginReturnedWork(a.ID, "")
			} else {
				err = s.RequestWorkReturn(a.ID, st.Seq, "Must reject")
			}
			if err == nil {
				t.Fatal("invalid work return accepted")
			}
			after, _ := s.Read()
			if after.Seq != before.Seq {
				t.Fatal("rejected return mutated state")
			}
		})
	}
}

func TestActualFourTurnWorkReturnLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Actual subprocess work return", func(st *State) error {
		st.Config.WorkReentry = true
		st.Config.Harness = "codex"
		st.Config.Command = []string{os.Args[0], "-test.run=TestWorkReturnCommandHelper", "work-return-fixture"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Execute(context.Background(), a, p); err != nil {
		t.Fatal(err)
	}
	st, err := Open(s.Dir).Read()
	if err != nil {
		t.Fatal(err)
	}
	x := st.Activations[a.ID]
	if st.Starts != 1 || x.WorkReturns != 1 || x.Status != "completed" || x.Completion == nil || st.Items["first-work"].ID == "" || st.Items["returned-work"].ID == "" {
		t.Fatal("incomplete loop", x)
	}
	if x.Usage.Input != 40 || x.Usage.Output != 4 || !x.Deadline.Equal(a.Deadline) {
		t.Fatal("usage or lease changed", x)
	}
	for _, phase := range []string{"work", "rectification", "work.return1", "rectification.return1"} {
		if _, err := os.Stat(s.path("harness-logs/" + a.ID + "." + phase + ".jsonl")); err != nil {
			t.Fatal("phase log lost", err)
		}
		var packet Packet
		b, err := os.ReadFile(s.path("contexts/" + a.ID + "." + phase + ".json"))
		if err != nil {
			t.Fatal(err)
		}
		if err = Decode(b, &packet); err != nil {
			t.Fatal(err)
		}
		if strings.HasPrefix(phase, "work") && !packet.Deadline.Equal(workBoundary(a)) {
			t.Fatal("work reserve changed", phase)
		}
	}
	if _, _, err = s.Admit(); err == nil {
		t.Fatal("completed dormancy manufactured more work")
	}
}

func TestInterruptedReturnedWorkRecoversWithoutReplayingWork(t *testing.T) {
	s, a := returnFixture(t)
	st, _ := s.Read()
	if err := s.RequestWorkReturn(a.ID, st.Seq, "Act on changed evidence"); err != nil {
		t.Fatal(err)
	}
	if err := s.beginReturnedWork(a.ID, "temporary-session"); err != nil {
		t.Fatal(err)
	}
	if err := s.Mutate(a.ID, "Preserve partial returned work", Batch{Items: []ItemChange{{Item: itemFixture("partial-return", "An uncertain effect needs inspection, not repetition")}}}); err != nil {
		t.Fatal(err)
	}
	if err := s.Finish(a.ID, Outcome{}, context.Canceled); err != nil {
		t.Fatal(err)
	}
	s = Open(s.Dir)
	now := a.Started.Add(2 * time.Minute)
	s.Now = func() time.Time { return now }
	b, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if b.Phase != "rectification" || b.RecoveryOf != a.ID || b.WorkReturnReason != "" || b.WorkReturns != 0 || b.Session != "" || st.Items["partial-return"].ID == "" {
		t.Fatal("recovery replayed or lost state", b)
	}
	if p.Phase != "rectification" || !strings.Contains(string(Marshal(p)), "partial-return") {
		t.Fatal("recovery lacks partial work")
	}
	if err = s.RequestWorkReturn(b.ID, st.Seq, "Do not replay"); err == nil {
		t.Fatal("recovery returned to work")
	}
	if err = s.CompletePhase(b.ID, Completion{ExpectedSeq: st.Seq, Considered: []string{"partial-return"}, Continuation: "dormant", Coverage: "Uncertain effect retained without repeating", Reason: "Recovered state only"}); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(b.ID, Outcome{Summary: "Recovered without product work"}, nil); err != nil {
		t.Fatal(err)
	}
	st, _ = Open(s.Dir).Read()
	if st.Starts != 2 || len(st.Activations) != 2 {
		t.Fatal("unexpected admission accounting")
	}
}

func TestWorkReturnCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "work-return-fixture") {
		return
	}
	if strings.HasSuffix(strings.Join(os.Args, " "), "login status") {
		fmt.Print("Logged in using ChatGPT") // Synthetic adapter fixture, never real cognition evidence.
		os.Exit(0)
	}
	var p Packet
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		panic(err)
	}
	start := strings.Index(string(raw), "\n\n{")
	if start < 0 {
		panic("packet absent")
	}
	if err := json.NewDecoder(strings.NewReader(string(raw[start+2:]))).Decode(&p); err != nil {
		panic(err)
	}
	// Schema discovery is available, not a compulsory per-turn ceremony. This
	// subprocess exercises all four actual harness prompts and default packets.
	if strings.Contains(string(raw[:start]), "Call contract for schemas") || strings.Contains(p.Guidance, "Read contract for schemas") {
		panic("harness requires a redundant contract read before valid tool use")
	}
	s := Open(os.Getenv("CONCORDE3_INSTANCE"))
	st, err := s.Read()
	if err != nil {
		panic(err)
	}
	a := st.Activations[p.Activation]
	out := Outcome{Summary: "Actual subprocess phase " + turnName(a), Usage: Usage{Quality: "measured", Basis: "synthetic", Input: 10, Output: 1}}
	if p.Phase == "work" {
		id := "first-work"
		if a.WorkReturns > 0 {
			id = "returned-work"
		}
		if err := s.Mutate(a.ID, "Useful actual work", Batch{Items: []ItemChange{{Item: itemFixture(id, "Useful work persisted")}}}); err != nil {
			panic(err)
		}
	} else if a.WorkReturns == 0 {
		if _, err := s.Call(context.Background(), a.ID, "resume_work", Marshal(map[string]any{"expected_seq": st.Seq, "reason": "Handle newly discovered work"})); err != nil {
			panic(err)
		}
	} else {
		if err := s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Considered: []string{"first-work", "returned-work"}, Continuation: "dormant", Coverage: "Useful work finished", Reason: "Final rectification"}); err != nil {
			panic(err)
		}
	}
	output := ""
	for i, arg := range os.Args {
		if arg == "--output-last-message" {
			output = os.Args[i+1]
		}
	}
	if output == "" {
		panic("output absent")
	}
	if err := os.WriteFile(output, Marshal(map[string]string{"summary": out.Summary}), 0600); err != nil {
		panic(err)
	}
	fmt.Println(`{"type":"thread.started","thread_id":"same-synthetic-session"}`)
	fmt.Println(`{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":1}}`)
	os.Exit(0)
}
