// The player's few icons (decorative: every button says what it does in its label).
export default function Icon({ name }) {
  const p = {
    play: <path d="M7 4.5v15l12-7.5z" fill="currentColor" />,
    pause: (
      <>
        <rect x="6" y="4.5" width="4" height="15" rx="1" fill="currentColor" />
        <rect x="14" y="4.5" width="4" height="15" rx="1" fill="currentColor" />
      </>
    ),
    prev: <path d="M6 5v14M18 5.5 9 12l9 6.5z" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />,
    next: <path d="M18 5v14M6 5.5 15 12l-9 6.5z" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />,
    sound: <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4zM15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />,
    muted: <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4zM16 9.5l5 5M21 9.5l-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />,
    close: <path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />,
  }[name]
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      {p}
    </svg>
  )
}
