import React, { useState } from "react";
import type { CareRanges } from "../types";

interface Props {
  ranges: CareRanges;
  source?: string;
  onChange: (updated: CareRanges) => void;
  readOnly?: boolean;
}

interface RangeRowProps {
  label: string;
  unit: string;
  minKey: keyof CareRanges;
  maxKey: keyof CareRanges;
  ranges: CareRanges;
  readOnly: boolean;
  onChange: (key: keyof CareRanges, value: number | null) => void;
}

function RangeRow({ label, unit, minKey, maxKey, ranges, readOnly, onChange }: RangeRowProps) {
  const minVal = ranges[minKey] as number | null;
  const maxVal = ranges[maxKey] as number | null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
      <span style={{ width: 130, fontSize: 14, color: "#374151" }}>{label}</span>
      <input
        type="number"
        value={minVal ?? ""}
        disabled={readOnly}
        onChange={(e) => onChange(minKey, e.target.value === "" ? null : parseFloat(e.target.value))}
        placeholder="min"
        style={inputStyle(readOnly)}
      />
      <span style={{ color: "#9ca3af" }}>–</span>
      <input
        type="number"
        value={maxVal ?? ""}
        disabled={readOnly}
        onChange={(e) => onChange(maxKey, e.target.value === "" ? null : parseFloat(e.target.value))}
        placeholder="max"
        style={inputStyle(readOnly)}
      />
      <span style={{ fontSize: 13, color: "#6b7280", width: 40 }}>{unit}</span>
    </div>
  );
}

function inputStyle(disabled: boolean): React.CSSProperties {
  return {
    width: 80,
    padding: "4px 8px",
    border: "1px solid #d1d5db",
    borderRadius: 6,
    fontSize: 14,
    background: disabled ? "#f9fafb" : "#fff",
    color: disabled ? "#9ca3af" : "#111827",
  };
}

const SOURCE_LABELS: Record<string, string> = {
  openplantbook: "📖 OpenPlantbook",
  ai: "🤖 AI suggested",
  manual: "✏️ Manual",
  unknown: "⏳ Loading…",
};

export function CareRangesEditor({ ranges, source, onChange, readOnly = false }: Props) {
  const [editing, setEditing] = useState(false);
  const [local, setLocal] = useState<CareRanges>(ranges);

  const isReadOnly = readOnly || !editing;

  function handleChange(key: keyof CareRanges, value: number | null) {
    setLocal((prev) => ({ ...prev, [key]: value }));
  }

  function handleSave() {
    onChange(local);
    setEditing(false);
  }

  function handleCancel() {
    setLocal(ranges);
    setEditing(false);
  }

  // Sync local state when ranges change from outside (e.g. after API returns)
  React.useEffect(() => {
    if (!editing) setLocal(ranges);
  }, [ranges, editing]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: 13, color: "#6b7280" }}>
          Source: <strong>{source ? SOURCE_LABELS[source] ?? source : "—"}</strong>
        </div>
        {!readOnly && !editing && (
          <button onClick={() => setEditing(true)} style={btnStyle("outline")}>
            ✏️ Edit
          </button>
        )}
      </div>

      <RangeRow label="Soil Moisture" unit="%" minKey="soil_moisture_min" maxKey="soil_moisture_max" ranges={local} readOnly={isReadOnly} onChange={handleChange} />
      <RangeRow label="Temperature" unit="°C" minKey="temperature_min" maxKey="temperature_max" ranges={local} readOnly={isReadOnly} onChange={handleChange} />
      <RangeRow label="Air Humidity" unit="%" minKey="air_humidity_min" maxKey="air_humidity_max" ranges={local} readOnly={isReadOnly} onChange={handleChange} />
      <RangeRow label="Light" unit="lux" minKey="light_lux_min" maxKey="light_lux_max" ranges={local} readOnly={isReadOnly} onChange={handleChange} />

      {editing && (
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button onClick={handleSave} style={btnStyle("primary")}>Save</button>
          <button onClick={handleCancel} style={btnStyle("outline")}>Cancel</button>
        </div>
      )}
    </div>
  );
}

function btnStyle(variant: "primary" | "outline"): React.CSSProperties {
  return {
    padding: "6px 14px",
    borderRadius: 6,
    fontSize: 13,
    cursor: "pointer",
    border: variant === "primary" ? "none" : "1px solid #d1d5db",
    background: variant === "primary" ? "#16a34a" : "#fff",
    color: variant === "primary" ? "#fff" : "#374151",
    fontWeight: 500,
  };
}
