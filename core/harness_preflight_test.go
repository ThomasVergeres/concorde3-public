package core

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func TestHarnessPreflightCancellationIsNotAuthenticationFailure(t *testing.T) {
	for _, harness := range []string{"codex", "command"} {
		for _, deadline := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/deadline=%t", harness, deadline), func(t *testing.T) {
				s := fixture(t)
				st, _ := s.Read()
				cfg := st.Config
				cfg.Harness = harness
				cfg.Command = []string{os.Args[0], "-test.run=TestPreflightCommandHelper", "preflight-fixture", "unauthenticated"}
				ctx, cancel := context.WithCancel(context.Background())
				want := context.Canceled
				if deadline {
					cancel()
					ctx, cancel = context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
					want = context.DeadlineExceeded
				} else {
					cancel()
				}
				defer cancel()
				_, err := s.Harness(ctx, Activation{ID: "probe", Config: cfg}, Packet{})
				if !errors.Is(err, want) || strings.Contains(err.Error(), "requires subscription login") || strings.Contains(err.Error(), "must advertise") {
					t.Fatalf("context failure was misdiagnosed: want %v, got %v", want, err)
				}
			})
		}
	}
}

func TestHarnessPreflightExecutionErrorDoesNotInventAuthenticationDiagnosis(t *testing.T) {
	for _, harness := range []string{"codex", "command"} {
		t.Run(harness, func(t *testing.T) {
			s := fixture(t)
			st, _ := s.Read()
			cfg := st.Config
			cfg.Harness = harness
			cfg.Command = []string{os.Args[0], "-test.run=TestPreflightCommandHelper", "preflight-fixture", "execution-error"}
			_, err := s.Harness(context.Background(), Activation{ID: "probe", Config: cfg}, Packet{})
			if err == nil || !strings.Contains(err.Error(), "preflight failed") || strings.Contains(err.Error(), "requires subscription login") || strings.Contains(err.Error(), "synthetic-private-token") {
				t.Fatalf("execution error invented diagnosis or disclosed stderr: %v", err)
			}
		})
	}
}

func TestHarnessPreflightUnauthenticatedStatusRemainsFailClosed(t *testing.T) {
	s := fixture(t)
	st, _ := s.Read()
	cfg := st.Config
	cfg.Harness = "codex"
	cfg.Command = []string{os.Args[0], "-test.run=TestPreflightCommandHelper", "preflight-fixture", "unauthenticated"}
	_, err := s.Harness(context.Background(), Activation{ID: "probe", Config: cfg}, Packet{})
	if err == nil || !strings.Contains(err.Error(), "requires subscription login") {
		t.Fatalf("unverified subscription must not proceed: %v", err)
	}
}

func TestPreflightCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "preflight-fixture") {
		return
	}
	if strings.Contains(strings.Join(os.Args, " "), "execution-error") {
		fmt.Fprintln(os.Stderr, "synthetic-private-token")
		os.Exit(7)
	}
	fmt.Print("Not logged in")
	os.Exit(0)
}

func TestCanceledPreflightActivationRecoveryAndFreshWriteback(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "config", "Synthetic preflight cancellation qualification", func(st *State) error {
		st.Config.Harness = "codex"
		st.Config.Command = []string{os.Args[0], "-test.run=TestPreflightCommandHelper", "preflight-fixture", "unauthenticated"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	a, packet, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Mutate(a.ID, "Useful state before interruption", Batch{Items: []ItemChange{{Item: itemFixture("surviving", "Provider outcome remains uncertain")}}}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err = s.Execute(ctx, a, packet)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled preflight did not retain its cause: %v", err)
	}
	s = Open(s.Dir)
	st, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	failed := st.Activations[a.ID]
	if failed.Status != "failed" || failed.Phase != "rectification_pending" || !strings.Contains(failed.Summary, "context canceled") || strings.Contains(failed.Summary, "requires subscription login") || st.Items["surviving"].ID == "" {
		t.Fatalf("durable interrupted state lost or misdiagnosed: %+v", failed)
	}
	// Harness replacement is explicit test machinery. A fresh process has no
	// former conversation; recovery reads the same canonical state and then
	// a later normal work/rectification loop must still commit useful output.
	if err = s.Update("operator", "config", "Use successful synthetic continuation harness", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestCommandHelper", "command-fixture"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	now := failed.RetryAt.Add(time.Second)
	s.Now = func() time.Time { return now }
	if err = s.Recover(); err != nil {
		t.Fatal(err)
	}
	b, packet, err := s.Admit()
	if err != nil || b.RecoveryOf != a.ID || b.Phase != "rectification" {
		t.Fatalf("missing fresh recovery: %+v %v", b, err)
	}
	if err = s.Execute(context.Background(), b, packet); err != nil {
		t.Fatal(err)
	}
	if err = s.Notify("purpose", "continue-after-recovery", "Continue existing undertaking after recovery"); err != nil {
		t.Fatal(err)
	}
	c, packet, err := s.Admit()
	if err != nil || c.Phase != "work" || c.RecoveryOf != "" {
		t.Fatalf("fresh work did not resume: %+v %v", c, err)
	}
	if err = s.Execute(context.Background(), c, packet); err != nil {
		t.Fatal(err)
	}
	st, err = Open(s.Dir).Read()
	if err != nil || st.Items["surviving"].ID == "" || st.Items["subprocess"].ID == "" || st.Activations[b.ID].Status != "completed" || st.Activations[c.ID].Status != "completed" {
		t.Fatalf("full recovery/writeback failed: %v", err)
	}
}
