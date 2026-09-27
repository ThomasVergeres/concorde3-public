package core

import (
	_ "embed"
	"fmt"
	"sort"
	"strings"
)

//go:embed kits/memory.md
var MemoryPractice string

//go:embed kits/rectification.md
var RectificationPractice string

//go:embed kits/capabilities.md
var CapabilityDescription string

type NodeView struct {
	Node  Node   `json:"node"`
	Items []Item `json:"items"`
}

// Describe the rejected candidate including runtime-added provenance, rather
// than asking a caller to estimate its size from statement text alone. Nothing
// is compacted, moved or removed automatically.
func nodeBoundError(v NodeView, total int) error {
	type itemSize struct {
		id    string
		bytes int
	}
	items := make([]itemSize, 0, len(v.Items))
	textBytes, sourceBytes, reasonBytes := 0, 0, 0
	for _, item := range v.Items {
		items = append(items, itemSize{item.ID, len(Marshal(item))})
		textBytes += len(Marshal(item.Text))
		reasonBytes += len(Marshal(item.Reason))
		if len(item.Sources) > 0 {
			sourceBytes += len(Marshal(item.Sources))
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].bytes != items[j].bytes {
			return items[i].bytes > items[j].bytes
		}
		return items[i].id < items[j].id
	})
	largest := []string{}
	for _, item := range items[:min(2, len(items))] {
		largest = append(largest, fmt.Sprintf("%s:%d", item.id, item.bytes))
	}
	return fmt.Errorf("node %s renders %d bytes; must be <5000; over_by=%d; node_bytes=%d; item_text_bytes=%d; item_sources_bytes=%d; item_reason_bytes=%d; largest_items=[%s] (up to 2, full rendered bytes). Sources and runtime provenance count; shorten/split/move explicitly or reference an artifact", v.Node.ID, total, total-4999, len(Marshal(v.Node)), textBytes, sourceBytes, reasonBytes, strings.Join(largest, ","))
}

func NodeContext(s State, id string) NodeView {
	v := NodeView{Node: s.Nodes[id], Items: []Item{}}
	for _, i := range s.Items {
		if i.Node == id && i.Status != "retired" {
			v.Items = append(v.Items, i)
		}
	}
	sort.Slice(v.Items, func(i, j int) bool { return v.Items[i].ID < v.Items[j].ID })
	return v
}
func (s State) HasRef(id string) bool { return s.Nodes[id].ID != "" || s.Items[id].ID != "" }
