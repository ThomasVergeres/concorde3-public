package core

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
	"time"
	"unicode"
)

type EmbeddingIndex struct {
	CommandHash string                    `json:"command_hash"`
	Entries     map[string]EmbeddingEntry `json:"entries"`
}
type EmbeddingEntry struct {
	Revision  int       `json:"revision"`
	InputHash string    `json:"input_hash,omitempty"`
	Vector    []float64 `json:"vector"`
}
type Hit struct {
	ID       string   `json:"id"`
	Title    string   `json:"title"`
	Summary  string   `json:"summary"`
	Revision int      `json:"revision"`
	Score    float64  `json:"score"`
	Sources  []Source `json:"sources,omitempty"`
}
type SearchResult struct {
	Hits         []Hit  `json:"hits"`
	Total        int    `json:"total"`
	Next         int    `json:"next_offset"`
	Semantic     string `json:"semantic"`
	StaleEntries int    `json:"stale_entries"`
}

func words(s string) []string {
	return strings.FieldsFunc(strings.ToLower(s), func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsNumber(r) })
}
func containsFold(s, sub string) bool {
	return sub != "" && strings.Contains(strings.ToLower(s), strings.ToLower(sub))
}
func lexicalHits(st State, query string) []Hit {
	terms := words(query)
	hits := []Hit{}
	for id, i := range st.Items {
		if i.Status == "retired" {
			continue
		}
		text := strings.ToLower(i.Text + " " + st.Nodes[i.Node].Title)
		score := 0.0
		for _, w := range terms {
			if len(w) > 2 && strings.Contains(text, w) {
				score += 1 / float64(max(1, len(terms)))
			}
		}
		for _, binding := range i.AppliesTo {
			if binding == "*" || binding == query || strings.Contains(strings.ToLower(query), strings.ToLower(binding)) {
				score += 2
			}
		}
		if score > 0 {
			hits = append(hits, Hit{ID: id, Title: st.Nodes[i.Node].Title, Summary: i.Text, Revision: i.Revision, Score: score, Sources: i.Sources})
		}
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].Score != hits[j].Score {
			return hits[i].Score > hits[j].Score
		}
		return hits[i].ID < hits[j].ID
	})
	return hits
}
func cosine(a, b []float64) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0
	}
	dot, aa, bb := 0.0, 0.0, 0.0
	for i, x := range a {
		dot += x * b[i]
		aa += x * x
		bb += b[i] * b[i]
	}
	if aa == 0 || bb == 0 {
		return 0
	}
	return dot / math.Sqrt(aa*bb)
}
func embeddings(ctx context.Context, command []string, dir string, texts []string) ([][]float64, error) {
	if len(command) == 0 {
		return nil, fmt.Errorf("semantic adapter not configured")
	}
	input, _ := json.Marshal(map[string]any{"texts": texts})
	out, _, err := RunCommand(ctx, command, dir, SafeEnvironment(), input, 4<<20, nil)
	if err != nil {
		return nil, err
	}
	var r struct {
		Vectors [][]float64 `json:"vectors"`
	}
	if err = Decode(out, &r); err != nil {
		return nil, err
	}
	if len(r.Vectors) != len(texts) {
		return nil, fmt.Errorf("embedding count mismatch")
	}
	dim := 0
	for _, v := range r.Vectors {
		if len(v) == 0 || len(v) > 65536 {
			return nil, fmt.Errorf("invalid vector dimension")
		}
		if dim == 0 {
			dim = len(v)
		}
		if len(v) != dim {
			return nil, fmt.Errorf("inconsistent vector dimensions")
		}
		for _, x := range v {
			if math.IsNaN(x) || math.IsInf(x, 0) {
				return nil, fmt.Errorf("invalid vector")
			}
		}
	}
	return r.Vectors, nil
}

// Node titles are part of the encoder input, but have revisions independent of
// their items. Cache validity must cover the entire actual input, not just the
// item's revision. Legacy entries without a fingerprint are rebuildable/stale.
func embeddingText(st State, item Item) string {
	return st.Nodes[item.Node].Title + "\n" + item.Text
}

func (s *Store) Reindex(ctx context.Context) error {
	st, e := s.Read()
	if e != nil {
		return e
	}
	index := EmbeddingIndex{Entries: map[string]EmbeddingEntry{}}
	cmd, _ := json.Marshal(st.Config.EmbeddingCommand)
	index.CommandHash = Digest(cmd)
	var prior EmbeddingIndex
	if b, err := os.ReadFile(s.path("search-index.json")); err == nil {
		_ = json.Unmarshal(b, &prior)
	}
	ids := []string{}
	for id, n := range st.Items {
		if n.Status != "retired" {
			if old, ok := prior.Entries[id]; ok && prior.CommandHash == index.CommandHash && old.Revision == n.Revision && old.InputHash == Digest([]byte(embeddingText(st, n))) {
				index.Entries[id] = old
			} else {
				ids = append(ids, id)
			}
		}
	}
	sort.Strings(ids)
	for start := 0; start < len(ids); start += 32 {
		end := min(start+32, len(ids))
		texts := []string{}
		for _, id := range ids[start:end] {
			n := st.Items[id]
			texts = append(texts, embeddingText(st, n))
		}
		vectors, e := embeddings(ctx, st.Config.EmbeddingCommand, s.Dir, texts)
		if e != nil {
			return e
		}
		for j, id := range ids[start:end] {
			index.Entries[id] = EmbeddingEntry{Revision: st.Items[id].Revision, InputHash: Digest([]byte(texts[j])), Vector: vectors[j]}
		}
	}
	b, _ := json.Marshal(index)
	return Atomic(s.path("search-index.json"), b)
}
func (s *Store) Search(ctx context.Context, query string, offset, limit int) (SearchResult, error) {
	st, e := s.Read()
	if e != nil {
		return SearchResult{}, e
	}
	r := SearchResult{Hits: []Hit{}, Semantic: "unavailable: no semantic adapter configured", Next: -1}
	terms := words(query)
	index := EmbeddingIndex{}
	var vector []float64
	if len(st.Config.EmbeddingCommand) > 0 {
		b, err := os.ReadFile(s.path("search-index.json"))
		cmd, _ := json.Marshal(st.Config.EmbeddingCommand)
		if err != nil || json.Unmarshal(b, &index) != nil || index.CommandHash != Digest(cmd) {
			r.Semantic = "unavailable: missing/invalid index; reindex required"
		} else {
			c, cancel := context.WithTimeout(ctx, 30*time.Second)
			vs, err := embeddings(c, st.Config.EmbeddingCommand, s.Dir, []string{query})
			cancel()
			if err != nil {
				r.Semantic = "unavailable: semantic adapter failed; lexical fallback"
			} else {
				vector = vs[0]
				r.Semantic = "available: revision-checked command embeddings"
			}
		}
	}
	hits := []Hit{}
	for id, n := range st.Items {
		if n.Status == "retired" {
			continue
		}
		text := strings.ToLower(st.Nodes[n.Node].Title + " " + n.Text)
		score := 0.0
		for _, w := range terms {
			if strings.Contains(text, w) {
				score += 1 / float64(max(1, len(terms)))
			}
		}
		if v, ok := index.Entries[id]; ok {
			if v.Revision != n.Revision || v.InputHash != Digest([]byte(embeddingText(st, n))) {
				r.StaleEntries++
			} else if len(vector) > 0 {
				score += max(0, cosine(vector, v.Vector)) * 2
			}
		}
		if score > 0 {
			hits = append(hits, Hit{ID: id, Title: st.Nodes[n.Node].Title, Summary: n.Text, Revision: n.Revision, Score: score, Sources: n.Sources})
		}
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].Score != hits[j].Score {
			return hits[i].Score > hits[j].Score
		}
		return hits[i].ID < hits[j].ID
	})
	r.Total = len(hits)
	offset = min(max(0, offset), len(hits))
	end := min(offset+limit, len(hits))
	r.Hits = hits[offset:end]
	if end < len(hits) {
		r.Next = end
	}
	return r, nil
}
