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

func TestAdmissionWitnessUsesNativeReadinessWithoutWakingDormancy(t *testing.T) {
	for _, dormant := range []bool{false, true} {
		t.Run(map[bool]string{false: "due", true: "dormant"}[dormant], func(t *testing.T) {
			root := t.TempDir()
			dir := filepath.Join(root, "subjects", "test")
			s := core.Open(dir)
			if err := s.Init("Maintain a useful persistent undertaking"); err != nil {
				t.Fatal(err)
			}
			if err := s.Update("operator", "fixture", "explicit synthetic admission test", func(st *core.State) error {
				st.Config.FreezeAt = time.Now().Add(time.Hour)
				i := st.Items["purpose"]
				i.Attention.EffortState = "waiting"
				i.Attention.DeferredAt = time.Now().Add(-time.Hour)
				if dormant {
					i.Attention.EffortState = "dormant"
				}
				st.Items[i.ID] = i
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			b, _ := json.Marshal(map[string]any{"status": "prepared", "snapshot_sha256": strings.Repeat("a", 64), "subjects": map[string]any{"test": map[string]any{}}})
			if err := os.WriteFile(filepath.Join(root, "fork.json"), b, 0600); err != nil {
				t.Fatal(err)
			}
			proof, err := admissionWitness(dir)
			if dormant {
				if err == nil || !strings.Contains(err.Error(), "no ready intention") {
					t.Fatal(proof, err)
				}
			} else if err != nil || proof["intention"] != "purpose" {
				t.Fatal(proof, err)
			}
			st, err := s.Read()
			if err != nil {
				t.Fatal(err)
			}
			if st.Mode != "frozen" {
				t.Fatal("not frozen")
			}
			if dormant && len(st.Activations) != 0 {
				t.Fatal("invented an admission")
			}
			for _, a := range st.Activations {
				if a.Status != "canceled" || a.Process != (core.Process{}) {
					t.Fatal("harness run or live activation")
				}
			}
			seq := st.Seq
			if _, err = admissionWitness(dir); err == nil {
				t.Fatal("reused frozen witness")
			}
			after, _ := s.Read()
			if after.Seq != seq {
				t.Fatal("rejected retry mutated frozen state")
			}
		})
	}
}

func TestAdmissionWitnessRejectsUnpreparedOrUnnamedCopyWithoutMutation(t *testing.T) {
	for _, variant := range []string{"missing", "preparing", "unnamed", "bad-hash"} {
		t.Run(variant, func(t *testing.T) {
			root := t.TempDir()
			dir := filepath.Join(root, "subjects", "test")
			s := core.Open(dir)
			if err := s.Init("Retain this self without admitting work"); err != nil {
				t.Fatal(err)
			}
			before, _ := s.Read()
			if variant != "missing" {
				m := map[string]any{"status": "prepared", "snapshot_sha256": strings.Repeat("a", 64), "subjects": map[string]any{"test": map[string]any{}}}
				if variant == "preparing" {
					m["status"] = "preparing"
				}
				if variant == "unnamed" {
					m["subjects"] = map[string]any{"other": map[string]any{}}
				}
				if variant == "bad-hash" {
					m["snapshot_sha256"] = "not-a-hash"
				}
				b, _ := json.Marshal(m)
				if err := os.WriteFile(filepath.Join(root, "fork.json"), b, 0600); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := admissionWitness(dir); err == nil {
				t.Fatal("unprepared witness accepted")
			}
			after, _ := s.Read()
			if after.Seq != before.Seq || after.Mode != before.Mode {
				t.Fatal("rejected admission altered the self")
			}
		})
	}
}
