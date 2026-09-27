package core

import (
	"fmt"
	"strings"
	"time"
)

func workBoundary(a Activation) time.Time {
	return a.Started.Add(a.Deadline.Sub(a.Started) * 3 / 4)
}

func workReturnAllowed(a Activation, now time.Time) error {
	if !a.Config.WorkReentry {
		return fmt.Errorf("work return is disabled; operator opt-in work_reentry is required")
	}
	if a.Phase != "rectification" || a.Completion != nil {
		return fmt.Errorf("work return requires unfinished rectification")
	}
	if a.RecoveryOf != "" {
		return fmt.Errorf("rectification recovery cannot return to product work; preserve a later continuation")
	}
	if a.WorkReturns != 0 {
		return fmt.Errorf("one work return has already been used in this activation")
	}
	if !now.Before(workBoundary(a)) {
		return fmt.Errorf("original work-time boundary has passed; preserve final rectification and a feasible later continuation")
	}
	return nil
}

// Request only: the current model turn remains state-only until it ends.
func (s *Store) RequestWorkReturn(actor string, seq int64, reason string) error {
	return s.Update(actor, "activation.work_return_requested", reason, func(st *State) error {
		a := st.Activations[actor]
		if err := workReturnAllowed(a, s.Now()); err != nil {
			return err
		}
		if seq != st.Seq {
			return fmt.Errorf("sequence conflict: read current state and reconsider concurrent/new evidence")
		}
		if strings.TrimSpace(reason) == "" || len(reason) > 1000 {
			return fmt.Errorf("bounded nonempty work-return reason required (at most 1000 bytes)")
		}
		if a.WorkReturnReason != "" {
			return fmt.Errorf("work return already requested; end this turn without phase_complete, or complete rectification to cancel the request")
		}
		a.WorkReturnReason = reason
		st.Activations[actor] = a
		return nil
	})
}

func (s *Store) beginReturnedWork(actor, session string) error {
	return s.Update(actor, "activation.work_returned", "requested work resumes within the original lease", func(st *State) error {
		a := st.Activations[actor]
		if err := workReturnAllowed(a, s.Now()); err != nil {
			return err
		}
		if a.WorkReturnReason == "" {
			return fmt.Errorf("no work return requested")
		}
		a.Phase = "work"
		a.WorkReturns++
		a.Process = Process{}
		a.PreparedContext, a.PreparedContexts = "", nil
		if session != "" {
			a.Session = session
		}
		st.Activations[actor] = a
		return nil
	})
}

type Completion struct {
	ExpectedSeq  int64     `json:"expected_seq"`
	Considered   []string  `json:"considered"`
	Outstanding  []string  `json:"outstanding"`
	Continuation string    `json:"continuation"`
	NextAt       time.Time `json:"next_at,omitempty"`
	Coverage     string    `json:"coverage"`
	Reason       string    `json:"reason"`
}

// A requested wake is an executable scheduling commitment, not a forecast or
// a desire. Long-range desires remain ordinary memory; no finite horizon is
// imposed on instances without an explicit terminal freeze.
func checkWakeHorizon(c Config, at time.Time) error {
	if !at.IsZero() && !c.FreezeAt.IsZero() && !at.Before(c.FreezeAt) {
		return fmt.Errorf("requested wake %s is at/after terminal freeze %s and cannot execute; choose an earlier feasible return or explicitly leave coverage absent without an impossible wake", at.Format(time.RFC3339), c.FreezeAt.Format(time.RFC3339))
	}
	return nil
}

func (s *Store) BeginRectification(id, summary, session string) error {
	return s.Update(id, "activation.rectification", "work ended; return to whole", func(st *State) error {
		a := st.Activations[id]
		if a.Phase != "work" && a.Phase != "rectification" {
			return fmt.Errorf("invalid phase transition")
		}
		a.Phase = "rectification"
		a.WorkSummary = summary
		a.Session = session
		a.Process = Process{}
		st.Activations[id] = a
		return nil
	})
}

// Consideration is a report, not verified comprehension. Citing an existing
// source or timer neither reads/mutates it nor makes it a graph endpoint.
func knownConsideredReference(st State, id string) bool {
	if st.HasRef(id) || st.Edges[id].ID != "" || st.Activations[id].ID != "" || st.Receipts[id].Key != "" || st.Consequences[id].ID != "" || st.Wakes[id].ID != "" || st.Programs[id].ID != "" || st.Timers[id].ID != "" {
		return true
	}
	for _, item := range st.Items {
		for _, source := range item.Sources {
			if id != "" && source.Ref == id {
				return true
			}
		}
	}
	return false
}

func (s *Store) CompletePhase(actor string, c Completion) error {
	_, err := s.completePhase(actor, c)
	return err
}

// Capture the resulting attention view inside the same transaction. A read
// after Update could instead describe another actor's intervening decision.
func (s *Store) completePhase(actor string, c Completion) (Pursuit, error) {
	var attention Pursuit
	err := s.Update(actor, "activation.rectified", c.Reason, func(st *State) error {
		a := st.Activations[actor]
		if a.Phase != "rectification" {
			return fmt.Errorf("phase_complete is only available in the rectification turn")
		}
		if a.Completion != nil {
			return fmt.Errorf("rectification already completed")
		}
		if c.ExpectedSeq != st.Seq {
			return fmt.Errorf("sequence conflict: read current state and reconsider concurrent/new evidence")
		}
		if c.Coverage == "" || len(Marshal(c)) > 4000 {
			return fmt.Errorf("bounded completion with explicit coverage (including uncovered) required")
		}
		for _, id := range c.Considered {
			if !knownConsideredReference(*st, id) {
				return fmt.Errorf("unknown considered reference %s: use existing canonical record IDs or exact refs already retained in item.sources; other source inspection may be described in the summary without creating memory just to complete the form", id)
			}
		}
		for _, id := range c.Outstanding {
			if st.Consequences[id].ID == "" {
				return fmt.Errorf("outstanding implication must reference a durable consequence: %s", id)
			}
		}
		i := st.Items[a.Pursuit]
		if i.Attention == nil {
			return fmt.Errorf("selected intention lost attention record")
		}
		switch c.Continuation {
		case "continue":
			i.Attention.EffortState = "ready"
		case "wait":
			i.Attention.EffortState = "waiting"
		case "dormant":
			i.Attention.EffortState = "dormant"
		case "stop":
			i.Attention.EffortState = "stopped"
		default:
			return fmt.Errorf("continuation must be continue, wait, dormant or stop; redirect via intention mutation")
		}
		if (c.Continuation == "dormant" || c.Continuation == "stop") && !c.NextAt.IsZero() {
			return fmt.Errorf("dormant/stop cannot have next_at; use wait for a dated return or an independent timer for event-only dormancy")
		}
		if !c.NextAt.IsZero() && !c.NextAt.After(s.Now()) {
			return fmt.Errorf("next_at must be future")
		}
		if err := checkWakeHorizon(st.Config, c.NextAt); err != nil {
			return err
		}
		i.Attention.NextAt = c.NextAt
		i.Attention.DeferredAt = s.Now()
		i.Revision++
		i.Actor = actor
		i.Reason = c.Reason
		i.UpdatedAt = s.Now()
		st.Items[i.ID] = i
		a.Completion = &c
		a.WorkReturnReason = "" // Explicit final completion supersedes a pending request.
		st.Activations[actor] = a
		attention = st.Portfolio()[a.Pursuit]
		return nil
	})
	if err != nil {
		return Pursuit{}, err
	}
	return attention, nil
}
