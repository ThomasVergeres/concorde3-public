package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestPulseSupervisesProgramsCreatedDuringWork(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if err := s.Update("operator", "fixture", "one activation with late program", func(st *State) error {
		st.Config.Workspace = true
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestPulseHarnessHelper", "pulse-fixture"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	err := s.Run(ctx, true, nil)
	if _, e := os.Stat(filepath.Join(s.Dir, "program-ready")); e != nil {
		t.Fatalf("program registered during work never ran: %v (pulse: %v)", e, err)
	}
	if err != nil {
		t.Fatal(err)
	}
	st, err := Open(s.Dir).Read()
	if err != nil {
		t.Fatal(err)
	}
	if len(st.Activations) != 1 || st.Mode != "paused" {
		t.Fatalf("pulse escaped one activation: %d %s", len(st.Activations), st.Mode)
	}
	for _, a := range st.Activations {
		if a.Status != "completed" || a.Completion == nil {
			t.Fatal(a)
		}
	}
	for _, p := range st.Programs {
		if Alive(p.Process) {
			t.Fatal("program survived pulse cleanup")
		}
	}
}

func TestPulseHarnessHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "pulse-fixture") {
		return
	}
	if os.Args[len(os.Args)-1] == "--concorde-capabilities" {
		fmt.Print(`{"protocol":2,"continuation":true}`)
		os.Exit(0)
	}
	s := Open(os.Getenv("CONCORDE3_INSTANCE"))
	var p Packet
	if json.NewDecoder(os.Stdin).Decode(&p) != nil {
		os.Exit(2)
	}
	if p.Phase == "work" {
		err := s.Mutate("operator", "synthetic work starts a program", Batch{Programs: []Program{{ID: "late", Command: []string{os.Args[0], "-test.run=TestPulseProgramHelper", "pulse-program"}, Pursuit: "purpose", Enabled: true}}})
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		until := time.Now().Add(3 * time.Second)
		for {
			if _, err = os.Stat(filepath.Join(s.Dir, "program-ready")); err == nil {
				break
			}
			if time.Now().After(until) {
				fmt.Fprintln(os.Stderr, "program did not start during work")
				os.Exit(2)
			}
			time.Sleep(20 * time.Millisecond)
		}
		_ = json.NewEncoder(os.Stdout).Encode(Outcome{Summary: "observed program start"})
	} else {
		st, _ := s.Read()
		_ = json.NewEncoder(os.Stdout).Encode(Outcome{Summary: "rectified", Completion: &Completion{ExpectedSeq: st.Seq, Continuation: "continue", Coverage: "synthetic fixture complete", Reason: "keep attention eligible to check one-shot bound"}})
	}
	os.Exit(0)
}

func TestPulseProgramHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "pulse-program") {
		return
	}
	if os.WriteFile(filepath.Join(os.Getenv("CONCORDE3_INSTANCE"), "program-ready"), []byte("ready"), 0600) != nil {
		os.Exit(2)
	}
	time.Sleep(30 * time.Second)
	os.Exit(0)
}
