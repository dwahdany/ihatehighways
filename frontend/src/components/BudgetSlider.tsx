import { useId } from 'react'

interface BudgetSliderProps {
  value: number
  onChange: (value: number) => void
}

export default function BudgetSlider({ value, onChange }: BudgetSliderProps) {
  const id = useId()
  return (
    <div className="field">
      <div className="field-head">
        <label className="eyebrow" htmlFor={id}>
          Time you&rsquo;ll trade
        </label>
        <output className="slider-value" htmlFor={id}>
          +{value} min
        </output>
      </div>
      <input
        id={id}
        className="slider"
        type="range"
        min={0}
        max={120}
        step={5}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  )
}
