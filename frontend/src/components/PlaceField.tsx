import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'
import { useMapsLibrary } from '@vis.gl/react-google-maps'

export interface PlaceSelection {
  placeId: string
  label: string
}

interface Suggestion {
  placeId: string
  label: string
  /** Place from prediction.toPlace(); its first fetchFields concludes the session. */
  place: google.maps.places.Place
}

interface PlaceFieldProps {
  label: string
  value: PlaceSelection | null
  onChange: (value: PlaceSelection | null) => void
}

const DEBOUNCE_MS = 250

/**
 * Custom-styled combobox over the headless Places Autocomplete Data API
 * (AutocompleteSuggestion.fetchAutocompleteSuggestions + session tokens).
 */
export default function PlaceField({ label, value, onChange }: PlaceFieldProps) {
  const places = useMapsLibrary('places')
  const id = useId()
  const inputId = `${id}-input`
  const listId = `${id}-listbox`

  const [inputValue, setInputValue] = useState(value?.label ?? '')
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)

  // One session token per autocomplete session: created on the first keystroke
  // of a session, discarded after a selection concludes it.
  const sessionRef = useRef<google.maps.places.AutocompleteSessionToken | null>(null)
  const timerRef = useRef<number | null>(null)
  const requestIdRef = useRef(0)

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    },
    [],
  )

  const fetchSuggestions = useCallback(
    async (input: string) => {
      if (!places) return
      const requestId = ++requestIdRef.current
      if (!sessionRef.current) {
        sessionRef.current = new places.AutocompleteSessionToken()
      }
      try {
        const { suggestions: results } =
          await places.AutocompleteSuggestion.fetchAutocompleteSuggestions({
            input,
            sessionToken: sessionRef.current,
          })
        if (requestId !== requestIdRef.current) return
        const next: Suggestion[] = []
        for (const suggestion of results) {
          const prediction = suggestion.placePrediction
          if (!prediction) continue
          const place = prediction.toPlace()
          next.push({ placeId: place.id, label: prediction.text.text, place })
        }
        setSuggestions(next)
        setOpen(next.length > 0)
        setActiveIndex(next.length > 0 ? 0 : -1)
      } catch {
        if (requestId !== requestIdRef.current) return
        setSuggestions([])
        setOpen(false)
        setActiveIndex(-1)
      }
    },
    [places],
  )

  function handleInput(event: ChangeEvent<HTMLInputElement>) {
    const text = event.target.value
    setInputValue(text)
    if (value) onChange(null) // edited after picking → selection no longer valid
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    // Any text change invalidates in-flight results: without this, a fetch for
    // the previous text resolving during the debounce window would render
    // suggestions that no longer match the input.
    requestIdRef.current += 1
    const trimmed = text.trim()
    if (!trimmed) {
      setSuggestions([])
      setOpen(false)
      setActiveIndex(-1)
      return
    }
    timerRef.current = window.setTimeout(() => {
      void fetchSuggestions(trimmed)
    }, DEBOUNCE_MS)
  }

  function select(suggestion: Suggestion) {
    onChange({ placeId: suggestion.placeId, label: suggestion.label })
    setInputValue(suggestion.label)
    setSuggestions([])
    setOpen(false)
    setActiveIndex(-1)
    // Conclude the autocomplete session for billing: the session token used in
    // fetchAutocompleteSuggestions is automatically attached to the first
    // fetchFields call on a Place obtained via prediction.toPlace(). Without
    // this Details call, every keystroke bills at the per-request SKU.
    void suggestion.place.fetchFields({ fields: ['id'] }).catch(() => {})
    sessionRef.current = null // session concluded; next keystroke starts a fresh one
    requestIdRef.current += 1 // drop any in-flight results
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
  }

  function handleBlur() {
    // Cancel the pending debounce and invalidate in-flight fetches so a
    // post-blur resolution can't reopen the listbox over an unfocused field.
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    requestIdRef.current += 1
    setOpen(false)
    setActiveIndex(-1)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open && suggestions.length > 0) {
        setOpen(true)
        setActiveIndex(0)
      } else if (suggestions.length > 0) {
        setActiveIndex((i) => (i + 1) % suggestions.length)
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open && suggestions.length > 0) {
        setOpen(true)
        setActiveIndex(suggestions.length - 1)
      } else if (suggestions.length > 0) {
        setActiveIndex((i) => (i - 1 + suggestions.length) % suggestions.length)
      }
    } else if (event.key === 'Enter') {
      if (open && activeIndex >= 0 && activeIndex < suggestions.length) {
        event.preventDefault()
        select(suggestions[activeIndex])
      }
    } else if (event.key === 'Escape') {
      // Dismissal must also cancel the pending debounce and invalidate any
      // in-flight fetch (same as handleBlur), or the listbox would (re)open
      // when they resolve after the Escape.
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      requestIdRef.current += 1
      if (open) {
        event.preventDefault()
        setOpen(false)
        setActiveIndex(-1)
      }
    }
  }

  const activeOptionId = open && activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined

  return (
    <div className="field">
      <label className="eyebrow" htmlFor={inputId}>
        {label}
      </label>
      <div className="combobox">
        <input
          id={inputId}
          className="text-input"
          type="text"
          role="combobox"
          autoComplete="off"
          spellCheck={false}
          placeholder="Search a place…"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeOptionId}
          value={inputValue}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          disabled={!places}
        />
        {open && suggestions.length > 0 && (
          <ul className="listbox" id={listId} role="listbox" aria-label={`${label} suggestions`}>
            {suggestions.map((suggestion, index) => (
              <li
                key={suggestion.placeId}
                id={`${id}-option-${index}`}
                className="option"
                role="option"
                aria-selected={index === activeIndex}
                onMouseDown={(event) => {
                  event.preventDefault() // keep focus on the input
                  select(suggestion)
                }}
                onMouseEnter={() => setActiveIndex(index)}
              >
                {suggestion.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
