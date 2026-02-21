# fb2d GUI — Future Work Notes

## Motivation
- 2D spatial nature of fb2d (nested loop rectangles, mirror bounces) is hard to reason about in terminal
- Custom glyphs would free us from ASCII opcode constraints
- Visual editing with comments/annotations on grid regions
- Animated IP execution would make debugging much easier

## Recommended approach: browser-based (React + Canvas/SVG)
- Python backend (Flask or websocket) running fb2d-08.py simulator
- Browser frontend: grid editor, color-coded opcodes, animated IP
- Lowest dependency burden (Python + browser), nicest rendering
- Can prototype frontend as Claude artifact first to test interaction model

## Feature priorities
1. **Display & step**: Load .fb2d grid, step forward/back, see IP move with head positions highlighted
2. **Edit**: Click cells to set opcodes, drag to select regions, copy/paste blocks
3. **Annotations**: Overlay comments on grid regions (e.g., "outer loop body", "GP trail")
4. **Compiler integration**: Edit ifb source, see compiled grid update live
5. **Breakpoints & watch**: Set breakpoints on cells, watch variable values in DATA_ROW

## Custom glyph ideas
- Once in GUI, opcodes can be rendered as any Unicode/emoji/icon
- Could use directional arrows for head moves, colored shapes for mirror types
- GP-conditional mirrors could have distinct visual treatment from CL-conditional ones
