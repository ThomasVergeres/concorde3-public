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

func TestCommandMeasuredUsageSurvivesRectificationAndRestart(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Qualify replaceable harness telemetry", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestCommandUsageHelper", "usage-fixture", "measured"}
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
	got := st.Activations[a.ID]
	if got.Status != "completed" || got.Usage != (Usage{Quality: "measured", Basis: "api-reported", Input: 200, Cached: 160, Output: 20}) {
		t.Fatalf("phase usage lost through complete native loop: %+v", got.Usage)
	}
	if st.Items["usage-proof"].ID == "" {
		t.Fatal("actual work writeback missing")
	}
}

func TestCommandUsageDoesNotInventOrAcceptInvalidMeasurement(t *testing.T) {
	for _, mode := range []string{"missing", "partial", "negative", "overcached", "unknown-quality", "missing-basis"} {
		t.Run(mode, func(t *testing.T) {
			s := fixture(t)
			s.Now = func() time.Time { return time.Now().UTC() }
			st, _ := s.Read()
			cfg := st.Config
			cfg.Harness = "command"
			cfg.Command = []string{os.Args[0], "-test.run=TestCommandUsageHelper", "usage-fixture", mode}
			if err := s.Update("operator", "config", "Qualify measurement validation", func(st *State) error { st.Config = cfg; return nil }); err != nil {
				t.Fatal(err)
			}
			running(t, s)
			a, p, err := s.Admit()
			if err != nil {
				t.Fatal(err)
			}
			out, err := s.Harness(context.Background(), a, p)
			if mode == "missing" {
				if err != nil || out.Usage.Quality != "unavailable" {
					t.Fatalf("missing usage: %+v %v", out.Usage, err)
				}
			} else if mode == "partial" {
				if err != nil || out.Usage.Quality != "partial" || out.Usage.Input != 100 {
					t.Fatalf("partial floor lost: %+v %v", out.Usage, err)
				}
			} else if err == nil {
				t.Fatalf("invalid measurement accepted: %s %+v", mode, out.Usage)
			}
		})
	}
}

func TestCommandUsageHelper(t *testing.T) {
	joined := strings.Join(os.Args, " ")
	if !strings.Contains(joined, "usage-fixture") {
		return
	}
	if os.Args[len(os.Args)-1] == "--concorde-capabilities" {
		fmt.Print(`{"protocol":2,"continuation":true}`)
		os.Exit(0)
	}
	var p Packet
	_ = json.NewDecoder(os.Stdin).Decode(&p)
	u := Usage{Quality: "measured", Basis: "api-reported", Input: 100, Cached: 80, Output: 10}
	switch os.Args[len(os.Args)-1] {
	case "missing":
		u = Usage{}
	case "partial":
		u.Quality = "partial"
	case "negative":
		u.Input = -1
	case "overcached":
		u.Cached = 101
	case "unknown-quality":
		u.Quality = "guess"
	case "missing-basis":
		u.Basis = ""
	}
	out := Outcome{Summary: "Synthetic telemetry qualification", Usage: u}
	if p.Phase == "work" {
		out.Changes = Batch{Items: []ItemChange{{Item: itemFixture("usage-proof", "Durable useful result")}}}
	} else {
		st, _ := Open(os.Getenv("CONCORDE3_INSTANCE")).Read()
		out.Completion = &Completion{ExpectedSeq: st.Seq, Continuation: "wait", Coverage: "Test complete", Reason: "Adequate result"}
	}
	_ = json.NewEncoder(os.Stdout).Encode(out)
	os.Exit(0)
}

func TestFailedCommandUsageDoesNotClaimSubscription(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Qualify failed adapter accounting", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestCommandUsageHelper", "usage-fixture", "negative"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Execute(context.Background(), a, p); err == nil {
		t.Fatal("invalid telemetry should not complete")
	}
	st, err := Open(s.Dir).Read()
	if err != nil {
		t.Fatal(err)
	}
	u := st.Activations[a.ID].Usage
	if u.Basis != "command" || u.Quality == "measured" {
		t.Fatalf("failed command incorrectly counted: %+v", u)
	}
}
