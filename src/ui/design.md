# akibaCryptobot — Modern UI Design Brief

## Role

Act as a UI/UX Designer and Frontend Developer. You build ultra-lightweight, functional, and atmospheric modern web interfaces. Eradicate all bloat and heavy modern frameworks. Focus on performance, pure aesthetic, and data clarity.

## Product Context

This interface is the command dashboard for **akibaCryptobot**, an automated cryptocurrency trading system. It allows the user to monitor bot status, view live trades, check account balances, and observe active strategies (like the correlation risk matrix).

**Primary Purpose:** "Command and Control"

**Key Value Propositions:**
1. **Real-time execution stats** — High-speed rendering of active trades and system health.
2. **Minimalist efficiency** — Zero unnecessary scripts; designed for low latency.
3. **Information density** — Modern dashboard layouts (cards, charts, data tables) styled with a cyberpunk aesthetic.

---

## Aesthetic Preset — "Modern Matrix"

- **Identity:** A modern, sleek interpretation of the classic *Matrix* aesthetic. It is NOT a raw terminal emulator. It is a highly functional, widget-based modern dashboard that uses the color palette, typography, and atmospheric elements of a cyber/hacker interface.
- **Palette:**
  - Absolute Black `#000000` (Background)
  - Deep Matrix Green `#06120E` (Card/Surface Backgrounds)
  - Dark Green Border `#0A291A` (Card Borders)
  - Neon Green `#00FF41` (Primary Accents, Active Stats, Buttons)
  - Dim Green `#008F11` (Secondary Text, Table Headers)
- **Typography:**
  - Headings/Numbers: Modern geometric sans-serif (e.g., `Inter` or `Space Grotesk`).
  - Data/Monospace: `Fira Code` or `VT323` for monetary values, logs, and codes.
- **Image Mood:** Data-driven. Minimalist glowing charts, structured grid layouts, and stark contrasts. Overlays of subtle digital rain.

---

## Fixed Design System

### Visual Texture
- Absolute stark contrast. Solid black global background.
- Cards and containers use a deep, almost invisible green (`#06120E`) with thin, sharp borders (`border-[#0A291A]`).
- Apply a subtle `text-shadow: 0 0 5px rgba(0,255,65,0.5);` on primary neon typography for a phosphor CRT glow effect, without sacrificing modern legibility.

### Micro-Interactions
- Cards elevate slightly on hover `translate-y-[-2px]` with a transition.
- Buttons and interactive elements use thin neon borders with a translucent neon fill on hover (`bg-[#00FF41]/10`).
- Seamless transitions (`transition-all duration-200 ease-in-out`).

### Animation Lifecycle
- Native CSS transitions only. No heavy animation libraries.
- Simple HTML5 canvas for the background digital rain effect, kept at extremely low opacity (`0.05`) so it provides atmosphere without interfering with data readability.

---

## Component Architecture

### A. NAVBAR & HEADER
- **Layout:** Horizontal top bar with a glassmorphism effect (blur on deep green background).
- **Left:** "akibaCryptobot" wordmark.
- **Right:** System Health indicator (Pulsing neon green dot) and Global Bot Toggle switch (Play/Pause).

### B. THE DASHBOARD VIEW (Grid Layout)
A modern CSS Grid layout displaying critical data in structured cards.

1. **Top Row (Metric Cards):**
   - 4 horizontal cards showing vital stats:
     - **Total PnL:** (Large neon green number).
     - **Active Positions:** (e.g., "3 Pairs").
     - **24h Volume:** (Monospace format).
     - **System Uptime/Latency:** (Dual metric card).

2. **Main Content (Charts & Activity):**
   - **Left Column (Wide):** A large "Live PnL Chart" area (can use a lightweight chart library like Chart.js or just scaffold the container for now).
   - **Right Column (Narrow):** "Recent Actions Log". A sleek, scrolling list of recent bot activities (Buy/Sell/Stop-loss triggers) styled like modern notifications, NOT raw terminal text.

3. **Bottom Row (Data Tables):**
   - **Active Positions Table:** Columns for Pair, Entry Price, Current Price, ROI (%), and an Action button ("Close Position"). Clean rows with hover highlights.

### C. THE MATRIX ATMOSPHERE
- An underlying `<canvas>` element running the classic Matrix digital rain animation at incredibly low opacity (`0.05`) behind the main dashboard grid.

---

## Technical Requirements

- **Stack:** HTML5, Vanilla JavaScript, and Tailwind CSS.
- **Tailwind configuration:** Base colors defined in Tailwind config for absolute black, deep container greens, and neon greens. Use utility classes rigorously avoiding custom CSS as much as possible, applying raw CSS only for the text shadow glowing effect.
- **Architecture:** Keep it simple.
  - `index.html` (Markup and layout)
  - `style.css` (Tailwind inputs and custom CRT glowing effects)
  - `matrix.js` (Lightweight script purely for the background canvas rain)
  - `app.js` (Logic for handling real-time data display and mock interactions)
- **Responsive:** Modern CSS grid/flexbox that gracefully collapses into a single column on mobile devices.
