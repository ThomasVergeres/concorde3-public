// fixture is lab-only machinery. It never runs a model or enters production CLI.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/ThomasVergeres/concorde3-public/core"
)

type seed struct {
	Goal         string          `json:"goal"`
	Config       json.RawMessage `json:"config"`
	Changes      core.Batch      `json:"changes"`
	Summary      string          `json:"rectification_summary"`
	Receipts     []core.Receipt  `json:"receipts"`
	Settled      bool            `json:"settled_checkpoint,omitempty"`
	RecentStarts int             `json:"recent_starts,omitempty"`
	Wakes        []core.Wake     `json:"wakes,omitempty"`
	Watches      []struct {
		ID     string         `json:"id"`
		Source core.WatchSpec `json:"source"`
	} `json:"watches,omitempty"`
	Past []struct {
		Summary    string          `json:"summary"`
		Completion core.Completion `json:"completion"`
	} `json:"past_activations,omitempty"`
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	if len(os.Args) != 3 {
		return fmt.Errorf("usage: fixture create DIR | validate DIR | inspect SNAPSHOT | fork COPIED_DIR (JSON spec on stdin) | admission COPIED_DIR")
	}
	if os.Args[1] == "fork" {
		return forkInstance(os.Args[2])
	}
	if os.Args[1] == "historical-boundary" {
		return historicalBoundary(os.Args[2])
	}
	if os.Args[1] == "rectification-boundary" {
		return rectificationBoundary(os.Args[2])
	}
	if os.Args[1] == "admission" {
		proof, err := admissionWitness(os.Args[2])
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(proof)
	}
	if os.Args[1] == "inspect" {
		b, err := os.ReadFile(os.Args[2])
		if err != nil {
			return err
		}
		var st core.State
		if err = core.Decode(b, &st); err != nil {
			return err
		}
		if err = core.Validate(st); err != nil {
			return err
		}
		// Read-only incident inventory: never copy credentials, processes or sessions.
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"version": st.Version, "nodes": len(st.Nodes), "items": len(st.Items), "activations": len(st.Activations), "sha256": core.Digest(b)})
	}
	s := core.Open(os.Args[2])
	if os.Args[1] == "validate" {
		st, err := s.Read()
		if err != nil {
			return err
		}
		return core.Validate(st)
	}
	if os.Args[1] != "create" {
		return fmt.Errorf("unknown command")
	}
	var spec seed
	dec := json.NewDecoder(os.Stdin)
	dec.DisallowUnknownFields()
	if err := dec.Decode(&spec); err != nil {
		return err
	}
	if err := s.Init(spec.Goal); err != nil {
		return err
	}
	if err := s.Update("operator", "fixture.seed", "Constructed checkpoint; no prior model experience claimed", func(st *core.State) error {
		if err := core.Decode(spec.Config, &st.Config); err != nil {
			return err
		}
		// A partial config is decoded into the actual default, not a second schema.
		seq := st.Seq
		spec.Changes.ExpectedSeq = &seq
		at := time.Now().UTC().Add(-time.Hour)
		if spec.Settled {
			at = time.Now().UTC()
		}
		if err := core.Apply(st, spec.Changes, "operator", "Constructed history", at); err != nil {
			return err
		}
		for _, r := range spec.Receipts {
			st.Receipts[r.Key] = r
		}
		if spec.RecentStarts < 0 || spec.RecentStarts > 100 {
			return fmt.Errorf("invalid constructed recent starts")
		}
		for n := 0; n < spec.RecentStarts; n++ {
			id := fmt.Sprintf("act.recent-%03d", n)
			now := time.Now().UTC().Add(-5 * time.Minute)
			st.Activations[id] = core.Activation{ID: id, Pursuit: "purpose", Phase: "completed", Status: "completed", Started: now, Finished: now.Add(time.Second), Deadline: now.Add(time.Minute), Config: st.Config, Reason: "Constructed earlier attention expenditure", Usage: core.Usage{Quality: "unavailable", Basis: "constructed"}}
			st.Starts++
		}
		for _, w := range spec.Wakes {
			w.At = time.Now().UTC()
			w.ConsumedBy = ""
			st.Wakes[w.ID] = w
		}
		for n, p := range spec.Past {
			id := fmt.Sprintf("act.history-%03d", n)
			// Outside the live rolling hour; constructed earlier experience is
			// explicit in both arms and never charged as actual model usage.
			now := time.Now().UTC().Add(-2*time.Hour + time.Duration(n)*time.Minute)
			st.Activations[id] = core.Activation{ID: id, Pursuit: "purpose", Phase: "completed", Status: "completed", Started: now, Finished: now.Add(time.Second), Deadline: now.Add(time.Minute), Config: st.Config, WorkSummary: p.Summary, Completion: &p.Completion, Reason: "Constructed history, not prior model experience", Usage: core.Usage{Quality: "unavailable", Basis: "constructed"}}
		}
		if spec.Summary != "" || spec.Settled {
			// An explicitly assisted recovery checkpoint. It spends no live LLM calls,
			// but its inherited start remains visible and charged in the hour window.
			now := time.Now().UTC()
			st.Activations["act.checkpoint"] = core.Activation{ID: "act.checkpoint", Pursuit: "purpose", Phase: "rectification_pending", Status: "failed", WorkSummary: spec.Summary, Started: now.Add(-time.Minute), Finished: now.Add(-time.Second), Deadline: now.Add(-time.Second), Config: st.Config, Reason: "Constructed interrupted work; conversation unavailable", CodeRevision: core.BuildRevision(), Usage: core.Usage{Quality: "unavailable", Basis: "constructed"}}
			st.Starts++
			if spec.Settled {
				a := st.Activations["act.checkpoint"]
				a.Status = "completed"
				a.Phase = "completed"
				a.Finished = now
				a.Reason = "Constructed settled snapshot; no pending rectification or prior live conversation"
				st.Activations[a.ID] = a
			}
		}
		return nil
	}); err != nil {
		return err
	}
	for _, w := range spec.Watches {
		if _, err := s.RegisterWatch(context.Background(), "operator", w.ID, "purpose", "Constructed existing observer; source choice is fallible", w.Source); err != nil {
			return err
		}
		// RegisterWatch normally runs inside the production CLI. A seed helper
		// must not accidentally become the executable of a supervised observer.
		if err := s.Update("operator", "fixture.watch_executable", "Use production watch process, not seed helper", func(st *core.State) error {
			p := st.Programs[w.ID]
			p.Command = []string{"concorde3", "watch-source", s.Dir, w.ID}
			st.Programs[w.ID] = p
			return nil
		}); err != nil {
			return err
		}
	}
	return nil
}
