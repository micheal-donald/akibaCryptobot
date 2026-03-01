# akibaCryptobot — Minimal UI Design Brief

## Role

Act as a UI/UX Designer and Frontend Developer. You build ultra-lightweight, functional, and atmospheric minimal web interfaces. Eradicate all bloat and heavy modern frameworks. Focus on performance, pure aesthetic, and data clarity.

## Product Context

This interface is the command dashboard for **akibaCryptobot**, an automated cryptocurrency trading system. It allows the user to monitor bot status, view live trades, check account balances, and observe active strategies (like the correlation risk matrix).

**Primary Purpose:** "Command and Control"

**Key Value Propositions:**
1. **Real-time execution stats** — High-speed rendering of active trades and system health.
2. **Minimalist efficiency** — Zero unnecessary scripts; designed for low latency.
3. **Information density** — Terminal-like display of logs, positions, and market data.

---

## Aesthetic Preset — "Matrix Terminal"

- **Identity:** An homage to the classic *Matrix* digital rain and phosphor monochrome monitors. Utilitarian, secretive, high-tech, and heavily terminal-inspired.
- **Palette:** 
  - Absolute Black `#000000` (Background)
  - Dark Green `#0D1A15` (Containers/Surfaces)
  - Neon Green `#00FF41` (Primary Text/Accents)
  - Dim Green `#008F11` (Secondary Text)
- **Typography:** Strictly monospaced. `Courier New`, `Consolas`, `Fira Code`, or `VT323`. 
- **Image Mood:** No photographic imagery. The interface relies purely on typography, borders, and CSS glow effects.

---

## Fixed Design System

### Visual Texture
- Absolute stark contrast. Solid black background.
- Apply a subtle `text-shadow: 0 0 5px #00FF41, 0 0 10px #00FF41;` on primary text for a phosphor CRT glow effect.

### Micro-Interactions
- Blinking cursor (`_` or `█`) on active input lines.
- Ghost buttons using only 1px solid green borders, no background fills. Invert background/text on hover.

### Animation Lifecycle
- Native CSS transitions only. Absolutely no heavy animation libraries (no GSAP, no framer-motion, etc.).
- Simple hardware-accelerated CSS keyframes or lightweight HTML5 canvas for the background digital rain effect.

---

## Component Architecture

### A. HEADER "The Navigation Bar"
- **Minimal block:** "akibaCryptobot v1.0.x" on the left, "STATUS: ONLINE" with a pulsing green dot on the right.

### B. THE TERMINAL (Main Content View)
- A central `div` resembling a terminal window with a 1px solid green border.
- **Top section:** System vital stats in a grid format (Uptime, Active Pairs, Current PnL, API Latency).
- **Middle section:** A live-updating text feed of recent actions, mimicking logs:
  - `[14:05:49] BUY BTC/USDT @ 65000`
  - `[14:05:51] RISK MATRIX: Correlation updated`
- **Bottom section:** Input line for manual overrides or system commands (e.g., `> halt execution_`).

### C. THE MATRIX RAIN (Background Effect)
- An underlying `<canvas>` element running the classic Matrix digital rain animation at low opacity (e.g., `0.1` or `0.15`) to ensure it doesn't distract from the terminal data above it. 

---

## Technical Requirements

- **Stack:** HTML5, Vanilla JavaScript, and Tailwind CSS.
- **Tailwind configuration:** Base colors defined in Tailwind config for absolute black and neon greens. Use utility classes rigorously avoiding custom CSS as much as possible, applying raw CSS only for the text shadow glowing effect that Tailwind doesn't do easily.
- **Architecture:** Keep it simple.
  - `index.html` (Markup and layout)
  - `style.css` (Tailwind inputs and custom CRT glowing effects)
  - `matrix.js` (Lightweight script purely for the background canvas rain)
  - `app.js` (Logic for handling real-time data display and mock interactions)
- **Responsive:** Fluid full-screen container that works on mobile (stacking columns) and desktop. 
