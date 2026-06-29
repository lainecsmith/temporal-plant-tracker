import React, { useState } from "react";
import type { CareRanges } from "../types";

interface Props {
  ranges: CareRanges;
  source?: string;
  /** Per-metric AI reasoning — only passed when source === "ai" */
  reasoning?: Record<string, string>;
  /** Called when the user saves; must return a Promise so validation errors surface inline. */
  onChange: (updated: CareRanges) => void | Promise<void>;
  readOnly?: boolean;
}

interface RangeRowProps {
  label: string;
  unit: string;
  minKey: keyof CareRanges;
  maxKey: keyof CareRanges;
  ranges: CareRanges;
  readOnly: boolean;
  reasoning?: string;
  onChange: (key: keyof CareRanges, value: number | null) => void;
}

function RangeRow({ label, unit, minKey, maxKey, ranges, readOnly, reasoning, onChange }: RangeRowProps) {
  const minVal = ranges[minKey] as number | null;
  const maxVal = ranges[maxKey] as number | null;
  const [tooltipVisible, setTooltipVisible] = useState(false);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
      {/* Label + optional info icon */}
      <div style={{ width: 130, display: "flex", alignItems: "center", gap: 4 }}>
        <span style={{ fontSize: 14, color: "#374151" }}>{label}</span>
        {reasoning && (
          <span
            onMouseEnter={() => setTooltipVisible(true)}
            onMouseLeave={() => setTooltipVisible(false)}
            style={{ position: "relative", cursor: "help", lineHeight: 1 }}
          >
            <span style={{ fontSize: 12, color: "#60a5fa", fontWeight: 700, userSelect: "none" }}>ⓘ</span>
            {tooltipVisible && (
              <div
                style={{
                  position: "absolute",
                  bottom: "calc(100% + 6px)",
                  left: "50%",
                  transform: "translateX(-50%)",
                  zIndex: 50,
                  background: "#1e293b",
                  color: "#f1f5f9",
                  padding: "8px 11px",
                  borderRadius: 8,
                  fontSize: 12,
                  lineHeight: 1.5,
                  width: 240,
                  whiteSpace: "normal",
                  boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
                  pointerEvents: "none",
                }}
              >
                {/* Tail */}
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    left: "50%",
                    transform: "translateX(-50%)",
                    width: 0,
                    height: 0,
                    borderLeft: "6px solid transparent",
                    borderRight: "6px solid transparent",
                    borderTop: "6px solid #1e293b",
                  }}
                />
                {reasoning}
              </div>
            )}
          </span>
        )}
      </div>

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

interface WateringIntervalRowProps {
  value: number | null;
  readOnly: boolean;
  reasoning?: string;
  onChange: (value: number | null) => void;
}

function WateringIntervalRow({ value, readOnly, reasoning, onChange }: WateringIntervalRowProps) {
  const [tooltipVisible, setTooltipVisible] = useState(false);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
      <div style={{ width: 130, display: "flex", alignItems: "center", gap: 4 }}>
        <span style={{ fontSize: 14, color: "#374151" }}>Watering</span>
        {reasoning && (
          <span
            onMouseEnter={() => setTooltipVisible(true)}
            onMouseLeave={() => setTooltipVisible(false)}
            style={{ position: "relative", cursor: "help", lineHeight: 1 }}
          >
            <span style={{ fontSize: 12, color: "#60a5fa", fontWeight: 700, userSelect: "none" }}>ⓘ</span>
            {tooltipVisible && (
              <div
                style={{
                  position: "absolute",
                  bottom: "calc(100% + 6px)",
                  left: "50%",
                  transform: "translateX(-50%)",
                  zIndex: 50,
                  background: "#1e293b",
                  color: "#f1f5f9",
                  padding: "8px 11px",
                  borderRadius: 8,
                  fontSize: 12,
                  lineHeight: 1.5,
                  width: 240,
                  whiteSpace: "normal",
                  boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
                  pointerEvents: "none",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    left: "50%",
                    transform: "translateX(-50%)",
                    width: 0,
                    height: 0,
                    borderLeft: "6px solid transparent",
                    borderRight: "6px solid transparent",
                    borderTop: "6px solid #1e293b",
                  }}
                />
                {reasoning}
              </div>
            )}
          </span>
        )}
      </div>
      <input
        type="number"
        value={value ?? ""}
        disabled={readOnly}
        min={1}
        step={1}
        onChange={(e) => onChange(e.target.value === "" ? null : parseFloat(e.target.value))}
        placeholder="—"
        style={{ ...inputStyle(readOnly), width: 80 }}
      />
      <span style={{ fontSize: 13, color: "#6b7280" }}>days between waterings</span>
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

export function CareRangesEditor({ ranges, source, reasoning, onChange, readOnly = false }: Props) {
  const [editing, setEditing] = useState(false);
  const [local, setLocal] = useState<CareRanges>(ranges);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const isReadOnly = readOnly || !editing;

  function handleChange(key: keyof CareRanges, value: number | null) {
    setLocal((prev) => ({ ...prev, [key]: value }));
    // Clear any previous error when the user edits
    setSaveError(null);
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      await onChange(local);
      // Only close the editor on success
      setEditing(false);
    } catch (e) {
      // Validation rejected by the workflow Update handler — show inline error,
      // keep the editor open so the user can correct the values.
      setSaveError(e instanceof Error ? e.message : "Failed to save care ranges");
    } finally {
      setSaving(false);
    }
  }

  function handleCancel() {
    setLocal(ranges);
    setEditing(false);
    setSaveError(null);
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

      <RangeRow label="Soil Moisture" unit="%" minKey="soil_moisture_min" maxKey="soil_moisture_max" ranges={local} readOnly={isReadOnly} reasoning={reasoning?.soil_moisture_reasoning} onChange={handleChange} />
      <RangeRow label="Temperature" unit="°F" minKey="temperature_min" maxKey="temperature_max" ranges={local} readOnly={isReadOnly} reasoning={reasoning?.temperature_reasoning} onChange={handleChange} />
      <RangeRow label="Air Humidity" unit="%" minKey="air_humidity_min" maxKey="air_humidity_max" ranges={local} readOnly={isReadOnly} reasoning={reasoning?.air_humidity_reasoning} onChange={handleChange} />
      <RangeRow label="Light" unit="lux" minKey="light_lux_min" maxKey="light_lux_max" ranges={local} readOnly={isReadOnly} reasoning={reasoning?.light_lux_reasoning} onChange={handleChange} />
      <WateringIntervalRow
        value={local.watering_interval_days}
        readOnly={isReadOnly}
        reasoning={reasoning?.watering_interval_reasoning}
        onChange={(v) => handleChange("watering_interval_days", v)}
      />

      {editing && (
        <div style={{ marginTop: 12 }}>
          {saveError && (
            <div
              style={{
                marginBottom: 8,
                padding: "7px 10px",
                background: "#fef2f2",
                border: "1px solid #fca5a5",
                borderRadius: 6,
                fontSize: 13,
                color: "#b91c1c",
              }}
            >
              ⚠️ {saveError}
            </div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={handleSave} disabled={saving} style={btnStyle("primary", saving)}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button onClick={handleCancel} disabled={saving} style={btnStyle("outline", saving)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function btnStyle(variant: "primary" | "outline", disabled = false): React.CSSProperties {
  return {
    padding: "6px 14px",
    borderRadius: 6,
    fontSize: 13,
    cursor: disabled ? "not-allowed" : "pointer",
    border: variant === "primary" ? "none" : "1px solid #d1d5db",
    background: variant === "primary" ? (disabled ? "#86efac" : "#16a34a") : "#fff",
    color: variant === "primary" ? "#fff" : (disabled ? "#9ca3af" : "#374151"),
    fontWeight: 500,
    opacity: disabled ? 0.7 : 1,
  };
}
