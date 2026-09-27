package core

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestZeroExitDoesNotHideProgramDiagnostics(t *testing.T) {
	s := fixture(t)
	now := s.Now()
	if err := s.Update("operator", "fixture", "masked process failure", func(st *State) error {
		st.Config.Workspace = true
		st.Programs["watcher"] = Program{ID: "watcher", Pursuit: "purpose", Enabled: true, IntervalSeconds: 30, Command: []string{"sh", "-c", "printf 'cannot evaluate input: missing dependency\\n' >&2; exit 0"}}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	if err := s.RunProgram(context.Background(), "watcher"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	var p map[string]any
	_ = Decode(Marshal(st.Programs["watcher"]), &p)
	if !strings.Contains(pString(p, "last_stderr"), "missing dependency") || len(st.Wakes) != 1 {
		t.Fatal("zero exit concealed diagnostic", p, st.Wakes)
	}
	// It remains a process success, not a deterministic declaration of business failure.
	if st.Programs["watcher"].Status != "succeeded" {
		t.Fatal("stderr was treated as a failed process")
	}
	r := Open(s.Dir)
	r.Now = func() time.Time { return now.Add(time.Minute) }
	if err := r.RunProgram(context.Background(), "watcher"); err != nil {
		t.Fatal(err)
	}
	st, _ = r.Read()
	if len(st.Wakes) != 1 {
		t.Fatal("repeated diagnostics flooded attention")
	}
	a, packet, err := r.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if len(packet.Wakes) != 1 {
		t.Fatal("diagnostic did not reach a real admission", packet)
	}
	complete(t, r, a, "wait")
}

func pString(p map[string]any, key string) string { v, _ := p[key].(string); return v }

func TestBenignChangingStderrIsNotARepeatedFailure(t *testing.T) {
	s := fixture(t)
	now := s.Now()
	if err := s.Update("operator", "fixture", "benign progress output", func(st *State) error {
		st.Config.Workspace = true
		st.Programs["progress"] = Program{ID: "progress", Pursuit: "purpose", Enabled: true, Command: []string{"sh", "-c", "printf 'progress from %s\\n' \"$$\" >&2"}}
		st.Programs["quiet"] = Program{ID: "quiet", Pursuit: "purpose", Enabled: true, Command: []string{"sh", "-c", "exit 0"}}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	for _, name := range []string{"progress", "quiet"} {
		if err := s.RunProgram(context.Background(), name); err != nil {
			t.Fatal(err)
		}
	}
	before, _ := s.Read()
	s.Now = func() time.Time { return now.Add(time.Minute) }
	if err := s.RunProgram(context.Background(), "progress"); err != nil {
		t.Fatal(err)
	}
	after, _ := s.Read()
	if len(after.Wakes) != 1 || after.Programs["progress"].Status != "succeeded" || after.Programs["quiet"].LastStderr != "" {
		t.Fatal("diagnostic became a health verdict or wake flood")
	}
	if before.Programs["progress"].LastStderr == after.Programs["progress"].LastStderr {
		t.Fatal("fixture did not vary the diagnostic")
	}
}
