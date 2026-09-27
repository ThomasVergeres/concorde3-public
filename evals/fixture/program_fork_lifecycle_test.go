package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/ThomasVergeres/concorde3-public/core"
)

// A real disposable foreground process; no model/network/provider is involved.
func TestProgramForkWitnessProcess(t *testing.T) {
	if os.Getenv("CONCORDE3_INSTANCE") == "" {
		t.Skip("subprocess witness only")
	}
	dir := os.Getenv("CONCORDE3_INSTANCE")
	source := filepath.Join(dir, "source.txt")
	old, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, "worker-ready"), []byte("ready"), 0600); err != nil {
		t.Fatal(err)
	}
	for {
		data, err := os.ReadFile(source)
		if err != nil {
			t.Fatal(err)
		}
		if string(data) != string(old) {
			if err = os.WriteFile(filepath.Join(dir, "observed.txt"), data, 0600); err != nil {
				t.Fatal(err)
			}
			if err = core.Open(dir).Notify("purpose", "fork-observation-"+core.Digest(data)[:16], "Ordinary source changed"); err != nil {
				t.Fatal(err)
			}
			old = data
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func awaitFork(t *testing.T, condition func() bool) {
	t.Helper()
	end := time.Now().Add(5 * time.Second)
	for time.Now().Before(end) {
		if condition() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("timed out awaiting actual program effect")
}

func TestProgramForkFreshProcessWakeRectificationRestartAndFreeze(t *testing.T) {
	source, spec := programForkFixture(t)
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	if err = source.Update("operator", "fixture", "register test executable, do not run source", func(st *core.State) error {
		p := st.Programs["processor"]
		p.Command = []string{exe, "-test.run=^TestProgramForkWitnessProcess$"}
		st.Programs[p.ID] = p
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	original, _ := source.Read()
	history, err := os.ReadFile(filepath.Join(source.Dir, core.Runtime, "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(t.TempDir(), "subject")
	if err = os.MkdirAll(filepath.Join(target, core.Runtime), 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"state.json", "events.jsonl"} {
		data, err := os.ReadFile(filepath.Join(source.Dir, core.Runtime, name))
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(target, core.Runtime, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	spec["resume_programs"] = []string{"processor"}
	if err = callForkSpec(t, target, spec); err != nil {
		t.Fatal(err)
	}
	s := core.Open(target)
	if err = s.SetMode("running"); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(target, "source.txt"), []byte("initial"), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	done := make(chan error, 1)
	go func() { done <- s.RunProgram(ctx, "processor") }()
	awaitFork(t, func() bool { _, err := os.Stat(filepath.Join(target, "worker-ready")); return err == nil })
	st, _ := s.Read()
	first := st.Programs["processor"].Process
	if first.PID == 0 || !core.Alive(first) {
		t.Fatal("no fresh program process")
	}
	if err = os.WriteFile(filepath.Join(target, "source.txt"), []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	key := "fork-observation-" + core.Digest([]byte("changed"))[:16]
	awaitFork(t, func() bool { st, err := s.Read(); return err == nil && st.Wakes[key].ID == key })
	a, p, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	served := false
	for _, w := range p.Wakes {
		if w.ID == key {
			served = true
		}
	}
	if !served {
		t.Fatal("ordinary program observation did not reach activation")
	}
	finishForkActivation(t, s, a.ID)
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("program did not stop on supervisor cancellation")
	}
	if core.Alive(first) {
		t.Fatal("old program still live")
	}

	reopened := core.Open(target)
	// The normal interrupted-program retry has a 30-second delay. Advance only
	// this mechanical fixture's clock; no production delay or historical date changes.
	reopened.Now = func() time.Time { return time.Now().Add(31 * time.Second) }
	if err = reopened.Recover(); err != nil {
		t.Fatal(err)
	}
	ctx2, cancel2 := context.WithCancel(context.Background())
	t.Cleanup(cancel2)
	done2 := make(chan error, 1)
	go func() { done2 <- reopened.RunProgram(ctx2, "processor") }()
	awaitFork(t, func() bool {
		st, err := reopened.Read()
		return err == nil && st.Programs["processor"].Process.PID != 0 && st.Programs["processor"].Process != first
	})
	st, _ = reopened.Read()
	second := st.Programs["processor"].Process
	// Give the new process a distinct ordinary input after its startup baseline.
	awaitFork(t, func() bool {
		info, err := os.Stat(filepath.Join(target, "worker-ready"))
		return err == nil && info.ModTime().After(a.Started)
	})
	if err = os.WriteFile(filepath.Join(target, "source.txt"), []byte("after restart"), 0600); err != nil {
		t.Fatal(err)
	}
	key = "fork-observation-" + core.Digest([]byte("after restart"))[:16]
	awaitFork(t, func() bool { st, err := reopened.Read(); return err == nil && st.Wakes[key].ID == key })
	if err = reopened.Freeze("finite witness boundary"); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done2:
	case <-time.After(5 * time.Second):
		t.Fatal("freeze left program running")
	}
	st, err = reopened.Read()
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode != "frozen" || st.Programs["processor"].Enabled || core.Alive(second) {
		t.Fatal("freeze did not stop copied operation")
	}
	after, _ := source.Read()
	afterHistory, _ := os.ReadFile(filepath.Join(source.Dir, core.Runtime, "events.jsonl"))
	if core.Digest(core.Marshal(original)) != core.Digest(core.Marshal(after)) || string(history) != string(afterHistory) {
		t.Fatal("original source mutated")
	}
}
