package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ThomasVergeres/concorde3-public/core"
)

func callForkSpec(t *testing.T, dir string, spec map[string]any) error {
	t.Helper()
	file, err := os.CreateTemp(t.TempDir(), "fork-input")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err = json.NewEncoder(file).Encode(spec); err != nil {
		t.Fatal(err)
	}
	if _, err = file.Seek(0, 0); err != nil {
		t.Fatal(err)
	}
	original := os.Stdin
	os.Stdin = file
	defer func() { os.Stdin = original }()
	return forkInstance(dir)
}

func programForkFixture(t *testing.T) (*core.Store, map[string]any) {
	t.Helper()
	root := t.TempDir()
	s := core.Open(filepath.Join(root, "trial", "subject"))
	if err := s.Init("Preserve a useful real operation"); err != nil {
		t.Fatal(err)
	}
	if err := s.SetMode("running"); err != nil {
		t.Fatal(err)
	}
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	finishForkActivation(t, s, a.ID)
	if err := s.Update("operator", "prepare", "synthetic stopped operation", func(st *core.State) error {
		st.Config.Workspace = true
		st.Mode = "frozen"
		st.Programs["processor"] = core.Program{ID: "processor", Command: []string{"/bin/true"}, Pursuit: "purpose", Status: "stopped", LastError: "old diagnosis"}
		st.Programs["observer"] = core.Program{ID: "observer", Command: []string{"/bin/true"}, Pursuit: "purpose", Status: "stopped", NextAt: time.Now().Add(5 * time.Minute)}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	source := strings.Repeat("a", 64)
	manifest := filepath.Join(root, "trial", "fork.json")
	raw, _ := json.Marshal(map[string]any{"snapshot_sha256": source, "status": "preparing"})
	if err := os.WriteFile(manifest, raw, 0600); err != nil {
		t.Fatal(err)
	}
	return s, map[string]any{"cutoff": time.Now().Add(30 * time.Minute).Unix(), "model": "gpt-5.6-luna", "effort": "xhigh", "starts": 6, "source_manifest": source, "manifest_path": manifest}
}

func finishForkActivation(t *testing.T, s *core.Store, id string) {
	t.Helper()
	if err := s.BeginRectification(id, "Synthetic fixture work retained", "fixture-session"); err != nil {
		t.Fatal(err)
	}
	st, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.CompletePhase(id, core.Completion{ExpectedSeq: st.Seq, Continuation: "wait", Coverage: "Synthetic fixture coverage only", Reason: "Preserve explicit continuation"}); err != nil {
		t.Fatal(err)
	}
	if err = s.Finish(id, core.Outcome{Summary: "Synthetic fixture complete"}, nil); err != nil {
		t.Fatal(err)
	}
}

func TestProgramForkRetainsRecentAdmissionUsage(t *testing.T) {
	s, spec := programForkFixture(t)
	spec["resume_programs"] = []string{}
	spec["starts"] = 1
	if err := callForkSpec(t, s.Dir, spec); err != nil {
		t.Fatal(err)
	}
	if err := s.SetMode("running"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.Admit(); err == nil || !strings.Contains(err.Error(), "hourly start limit") {
		t.Fatal("fork invented fresh hourly capacity", err)
	}
}

func TestProgramForkRequiresExplicitStoppedSelection(t *testing.T) {
	for _, names := range [][]string{{"processor"}, {}, {"observer"}} {
		s, spec := programForkFixture(t)
		before, _ := s.Read()
		if err := callForkSpec(t, s.Dir, spec); err == nil {
			t.Fatal("program-bearing source accepted without review")
		}
		spec["resume_programs"] = names
		if err := callForkSpec(t, s.Dir, spec); err != nil {
			t.Fatal(err)
		}
		after, err := s.Read()
		if err != nil {
			t.Fatal(err)
		}
		if after.Mode != "paused" || after.Seq != before.Seq+1 || after.Starts != before.Starts {
			t.Fatal("fork lost provenance or accounting")
		}
		for _, id := range []string{"processor", "observer"} {
			selected := false
			for _, name := range names {
				if name == id {
					selected = true
				}
			}
			if after.Programs[id].Enabled != selected {
				t.Fatal("wrong registrations restarted")
			}
		}
		if after.Programs["processor"].Process != (core.Process{}) || after.Programs["processor"].Status != "stopped" {
			t.Fatal("preparation ran a process")
		}
		if after.Programs["processor"].LastError != "old diagnosis" || after.Programs["observer"].NextAt != before.Programs["observer"].NextAt {
			t.Fatal("historical outcome/schedule erased")
		}
		if core.Digest(core.Marshal(after.Items)) != core.Digest(core.Marshal(before.Items)) || core.Digest(core.Marshal(after.Activations)) != core.Digest(core.Marshal(before.Activations)) {
			t.Fatal("rewrote knowledge/activation history")
		}
	}
}

func TestProgramForkRejectsAmbiguousOrLiveSourcesAtomically(t *testing.T) {
	for _, variant := range []string{"unknown", "duplicate", "enabled", "running", "handle", "activation_handle", "null", "stale_manifest"} {
		t.Run(variant, func(t *testing.T) {
			s, spec := programForkFixture(t)
			spec["resume_programs"] = []string{"processor"}
			switch variant {
			case "unknown":
				spec["resume_programs"] = []string{"missing"}
			case "duplicate":
				spec["resume_programs"] = []string{"processor", "processor"}
			case "null":
				spec["resume_programs"] = nil
			case "stale_manifest":
				spec["source_manifest"] = strings.Repeat("b", 64)
			default:
				if err := s.Update("operator", "fixture", "unsafe source variant", func(st *core.State) error {
					if variant == "activation_handle" {
						for id, a := range st.Activations {
							a.Process = core.Process{PID: 99999999, Start: "stale"}
							st.Activations[id] = a
							break
						}
						return nil
					}
					p := st.Programs["processor"]
					if variant == "enabled" {
						p.Enabled = true
					}
					if variant == "running" {
						p.Status = "running"
					}
					if variant == "handle" {
						p.Process = core.Process{PID: 99999999, Start: "stale"}
					}
					st.Programs[p.ID] = p
					return nil
				}); err != nil {
					t.Fatal(err)
				}
			}
			before, _ := s.Read()
			err := callForkSpec(t, s.Dir, spec)
			if err == nil {
				t.Fatal("unsafe source accepted")
			}
			want := "disabled/stopped"
			switch variant {
			case "unknown", "duplicate":
				want = "unknown or duplicate"
			case "null":
				want = "explicit resume_programs"
			case "stale_manifest":
				want = "preparing fork provenance"
			case "activation_handle":
				want = "activation process handle"
			}
			if !strings.Contains(err.Error(), want) {
				t.Fatal("rejected for wrong reason", err)
			}
			after, _ := s.Read()
			if core.Digest(core.Marshal(before)) != core.Digest(core.Marshal(after)) {
				t.Fatal("rejected fork mutated source")
			}
		})
	}
}
