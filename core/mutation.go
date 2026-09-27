package core

import (
	"fmt"
	"time"
)

type NodeChange struct {
	ExpectedRevision int  `json:"expected_revision"`
	Node             Node `json:"node"`
}
type ItemChange struct {
	ExpectedRevision int  `json:"expected_revision"`
	Item             Item `json:"item"`
}
type Ack struct {
	ID      string `json:"id"`
	Pursuit string `json:"intention"`
	Reason  string `json:"reason"`
}
type Batch struct {
	ExpectedSeq             *int64        `json:"expected_seq,omitempty"`
	Nodes                   []NodeChange  `json:"nodes,omitempty"`
	Items                   []ItemChange  `json:"items,omitempty"`
	Edges                   []Edge        `json:"edges,omitempty"`
	RemoveEdges             []string      `json:"remove_edges,omitempty"`
	Consequences            []Consequence `json:"consequences,omitempty"`
	Acknowledge             []Ack         `json:"acknowledge,omitempty"`
	AcknowledgeObservations []string      `json:"acknowledge_observations,omitempty"`
	Timers                  []Timer       `json:"timers,omitempty"`
	Programs                []Program     `json:"programs,omitempty"`
}

func Apply(st *State, b Batch, actor, reason string, now time.Time) error {
	if b.ExpectedSeq != nil && *b.ExpectedSeq != st.Seq {
		return fmt.Errorf("revision conflict: expected sequence %d, current %d; read and reconcile", *b.ExpectedSeq, st.Seq)
	}
	if len(b.AcknowledgeObservations) > 0 && b.ExpectedSeq == nil {
		return fmt.Errorf("observation acknowledgment requires expected_seq from current state")
	}
	seenObservations := map[string]bool{}
	for _, id := range b.AcknowledgeObservations {
		w, ok := st.Wakes[id]
		if !ok || seenObservations[id] || w.At.After(now) {
			return fmt.Errorf("observation acknowledgment requires distinct existing non-future IDs: %s", id)
		}
		seenObservations[id] = true
	}
	for _, id := range b.AcknowledgeObservations {
		w := st.Wakes[id]
		// Acknowledgment is explicit disposition of the wake, not a verdict on
		// the underlying duty. Preserve already-recorded consumers and evidence.
		if w.ConsumedBy == "" {
			w.ConsumedBy = actor
			st.Wakes[id] = w
		}
	}
	for _, ch := range b.Nodes {
		n := ch.Node
		old := st.Nodes[n.ID]
		if old.Revision != ch.ExpectedRevision {
			return fmt.Errorf("node %s revision conflict: current %d", n.ID, old.Revision)
		}
		n.Revision = old.Revision + 1
		n.Actor = actor
		n.Reason = reason
		n.UpdatedAt = now
		st.Nodes[n.ID] = n
	}
	for _, ch := range b.Items {
		i := ch.Item
		old := st.Items[i.ID]
		if old.Revision != ch.ExpectedRevision {
			return fmt.Errorf("item %s revision conflict: current %d", i.ID, old.Revision)
		}
		if (i.Attention != nil || old.Attention != nil) && b.ExpectedSeq == nil {
			return fmt.Errorf("attention edits require expected_seq from current state")
		}
		if i.Attention != nil {
			// The clock belongs to the runtime. Text/weight edits cannot silently
			// postpone consideration; changing the actual schedule can.
			a := *i.Attention
			if (a.EffortState == "dormant" || a.EffortState == "stopped") && !a.NextAt.IsZero() && (old.Attention == nil || a.EffortState != old.Attention.EffortState || !a.NextAt.Equal(old.Attention.NextAt)) {
				return fmt.Errorf("dormant/stopped cannot request next_at; use waiting for a dated return")
			}
			if old.Attention == nil || a.EffortState != old.Attention.EffortState || !a.NextAt.Equal(old.Attention.NextAt) {
				a.DeferredAt = now
			} else {
				a.DeferredAt = deferralAnchor(*st, old)
			}
			i.Attention = &a
		}
		if i.Attention != nil && (old.Attention == nil || !i.Attention.NextAt.Equal(old.Attention.NextAt)) {
			if err := checkWakeHorizon(st.Config, i.Attention.NextAt); err != nil {
				return err
			}
		}
		if old.Attention != nil && i.Attention == nil {
			for _, a := range st.Activations {
				if a.Pursuit == i.ID && a.Status == "running" {
					return fmt.Errorf("retain leased intention attention until completion")
				}
			}
		}
		i.Revision = old.Revision + 1
		i.Actor = actor
		i.Reason = reason
		i.UpdatedAt = now
		st.Items[i.ID] = i
	}
	for _, e := range b.Edges {
		if st.Edges[e.ID].ID != "" && b.ExpectedSeq == nil {
			return fmt.Errorf("editing an existing link requires expected_seq")
		}
		e.Reason = reason
		st.Edges[e.ID] = e
	}
	for _, id := range b.RemoveEdges {
		if b.ExpectedSeq == nil {
			return fmt.Errorf("removing links requires expected_seq")
		}
		delete(st.Edges, id)
	}
	for _, c := range b.Consequences {
		if !validID(c.ID) || c.Summary == "" || len(c.Summary) > 4000 || len(c.Targets) == 0 {
			return fmt.Errorf("consequence requires id, bounded summary and targets")
		}
		if _, ok := st.Consequences[c.ID]; ok {
			return fmt.Errorf("consequence %s already exists; acknowledge exact evidence or use a new id", c.ID)
		}
		for _, t := range c.Targets {
			// Preserve implications independently of funding attention, matching
			// Validate. Creating memory neither allocates nor wakes attention.
			if st.Items[t].Kind != "intention" || st.Items[t].Status == "retired" {
				return fmt.Errorf("unknown consequence target %s: target must be an existing non-retired intention item; attention allocation is not required", t)
			}
		}
		c.CreatedAt = now
		c.Actor = actor
		c.Acknowledgments = map[string]string{}
		st.Consequences[c.ID] = c
	}
	for _, a := range b.Acknowledge {
		c, ok := st.Consequences[a.ID]
		if !ok || a.Reason == "" {
			return fmt.Errorf("acknowledgment needs existing consequence and reason")
		}
		target := false
		for _, t := range c.Targets {
			target = target || t == a.Pursuit
		}
		if !target {
			return fmt.Errorf("not a consequence target")
		}
		if c.Acknowledgments == nil {
			c.Acknowledgments = map[string]string{}
		}
		c.Acknowledgments[a.Pursuit] = a.Reason
		st.Consequences[a.ID] = c
	}
	for _, t := range b.Timers {
		if t.Active {
			if err := checkWakeHorizon(st.Config, t.Due); err != nil {
				return err
			}
		}
		st.Timers[t.ID] = t
	}
	for _, p := range b.Programs {
		old := st.Programs[p.ID]
		if old.Process.PID != 0 {
			return fmt.Errorf("disable and wait for running program %s before editing", p.ID)
		}
		p.Process = Process{}
		p.Status = "idle"
		p.LastAt = old.LastAt
		if Digest(Marshal(p.Watch)) == Digest(Marshal(old.Watch)) {
			p.Observed = old.Observed
		} else {
			p.Observed = WatchState{}
		}
		if Digest(Marshal(p.Command)) == Digest(Marshal(old.Command)) {
			p.LastStderr = old.LastStderr
		} else {
			p.LastStderr = ""
		}
		st.Programs[p.ID] = p
	}
	return nil
}
func (s *Store) Mutate(actor, reason string, b Batch) error {
	return s.Update(actor, "mutation", reason, func(st *State) error { return Apply(st, b, actor, reason, s.Now()) })
}
func (s *Store) Notify(pursuit, key, evidence string) error {
	return s.Update("operator", "observation", "instance-owned observation", func(st *State) error {
		if st.Mode == "frozen" {
			return fmt.Errorf("instance frozen")
		}
		if !validID(key) || st.Portfolio()[pursuit].ID == "" || st.Portfolio()[pursuit].Status == "stopped" || len(evidence) > 4000 {
			return fmt.Errorf("valid key, live pursuit and bounded evidence required")
		}
		if w, ok := st.Wakes[key]; ok {
			if w.Pursuit != pursuit || w.Evidence != evidence {
				return fmt.Errorf("idempotency key reused with different observation")
			}
			return nil
		}
		st.Wakes[key] = Wake{ID: key, Pursuit: pursuit, Evidence: evidence, At: s.Now()}
		return nil
	})
}
