package core

import (
	"context"
	"fmt"
	"time"
)

// A free-form context query must not revoke already delivered action context.
// Relevant instruction revisions do invalidate it, so this is not a permanent bypass.
func contextStamp(st State, a Activation, activity string) string {
	refs := map[string]int{}
	for _, id := range append(append([]string{}, st.Config.Global...), st.Config.WorkPractice, a.Pursuit) {
		refs[id] = st.Items[id].Revision
	}
	for id, i := range st.Items {
		for _, b := range i.AppliesTo {
			if b == "*" || b == a.Pursuit || containsFold(activity, b) {
				refs[id] = i.Revision
				break
			}
		}
	}
	return Digest(Marshal(map[string]any{"activity": activity, "references": refs}))
}

// Refresh retrieves candidates outside the transaction, then resolves current
// canonical revisions. No index becomes truth and no semantic service is required.
func (s *Store) RefreshContext(ctx context.Context, actor, activity string) (Packet, error) {
	return s.refreshContext(ctx, actor, activity, false)
}

func (s *Store) refreshContext(ctx context.Context, actor, activity string, toolResponse bool) (Packet, error) {
	bound := func(st *State) {
		if toolResponse {
			// Reserve space for mediated-action deferral and MCP response wrapping.
			st.Config.ContextBytes = min(st.Config.ContextBytes, st.Config.ResponseBytes-1024)
		}
	}
	if actor != "operator" && activity != "" {
		if e := s.Update(actor, "context.activity", "refresh before activity change", func(st *State) error {
			a := st.Activations[actor]
			a.Activity = activity
			view := clone(*st)
			bound(&view)
			p := assembleAt(view, a, s.Now())
			if p.Error != "" {
				return fmt.Errorf("%s", p.Error)
			}
			st.Activations[actor] = a
			return nil
		}); e != nil {
			return Packet{}, e
		}
	}
	st, e := s.Read()
	if e != nil {
		return Packet{}, e
	}
	bound(&st)
	a := st.Activations[actor]
	if actor == "operator" {
		a = Activation{ID: "operator", Pursuit: st.Config.Reconsideration, Phase: "work", Activity: activity, Started: s.Now()}
	}
	if a.ID == "" {
		return Packet{}, fmt.Errorf("unknown activation")
	}
	// Leave space for associative candidates within the same packet limit.
	fullBudget := st.Config.ContextBytes
	st.Config.ContextBytes = max(4096, fullBudget-4000)
	p := assembleAt(st, a, s.Now())
	st.Config.ContextBytes = fullBudget
	if p.Error != "" {
		p = assembleAt(st, a, s.Now())
		if p.Error != "" {
			return p, fmt.Errorf("%s", p.Error)
		}
	}
	query := st.Items[a.Pursuit].Text + " " + st.Items[a.Pursuit].Approach + " " + activity + " " + a.WorkSummary
	refreshFailed := false
	if len(st.Config.EmbeddingCommand) > 0 {
		c, cancel := context.WithTimeout(ctx, 10*time.Second)
		// Incremental reindex only regenerates stale revisions; failures remain explicit.
		err := s.Reindex(c)
		cancel()
		if err != nil {
			refreshFailed = true
			p.Semantic = "unavailable: index refresh failed; lexical/graph fallback"
		}
	}
	c, cancel := context.WithTimeout(ctx, 10*time.Second)
	r, err := s.Search(c, query, 0, 12)
	cancel()
	if err == nil {
		p.Semantic = r.Semantic
		if refreshFailed {
			// A successful query can use unchanged entries from an older index.
			// That does not establish coverage of the failed refresh. Preserve both
			// facts rather than hiding the failure or discarding healthy candidates.
			p.Semantic = "degraded: index refresh failed; " + r.Semantic
		}
		fresh, e := s.Read()
		if e != nil {
			return p, e
		}
		for _, hit := range r.Hits {
			inactive := st.Config.RectifyPractice
			if a.Phase == "rectification" {
				inactive = st.Config.WorkPractice
			}
			if hit.ID == inactive {
				continue
			}
			found := false
			for _, x := range p.Items {
				if x.Item.ID == hit.ID {
					found = true
					break
				}
			}
			if found {
				continue
			}
			i, ok := fresh.Items[hit.ID]
			if !ok || i.Status == "retired" || i.Revision != hit.Revision {
				p.Omitted++
				continue
			}
			p.Items = append(p.Items, ContextItem{i, "automatic associative candidate; assess applicability"})
			if len(Marshal(p)) > fullBudget-128 {
				p.Items = p.Items[:len(p.Items)-1]
				p.Omitted++
			}
		}
	}
	return p, nil
}
