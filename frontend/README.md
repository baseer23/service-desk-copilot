# DeskMate Frontend

Minimal ChatGPT-like UI using Vite + React + TypeScript.

## How to run
```
npm install
npm run dev
```

Open http://localhost:5173

## Keyboard
- Enter: send message
- Shift+Enter: newline in the composer

## Voice & Speak
- Mic uses the Web Speech API for voice input (when supported by the browser).
- Speak uses `window.speechSynthesis` to read the last assistant reply.
- If your browser does not support these APIs, the buttons will be disabled or show a tooltip.

