import React, { useRef, useState } from "react";
import { Droplets, Thermometer, Wind, Sun, RefreshCw, ChevronDown, ChevronUp, MoreHorizontal } from "lucide-react";
import type { PlantState, CareRanges, PlantStatus } from "../types";
import { TERMINAL_STATUSES } from "../types";
import { CareRangesEditor } from "./CareRangesEditor";
import { AssociateSensorModal } from "./AssociateSensorModal";
import { api } from "../api/client";

interface Props {
  plant: PlantState;
  onUpdate: (updated: PlantState) => void;
  onRemove: (plantId: string) => void;
}

const STATUS_COLORS: Record<string, { bg: string; border: string; dot: string }> = {
  ok:         { bg: "#f0fdf4", border: "#86efac", dot: "#16a34a" },
  warning:    { bg: "#fffbeb", border: "#fcd34d", dot: "#d97706" },
  unknown:    { bg: "#f8fafc", border: "#cbd5e1", dot: "#94a3b8" },
  dead:       { bg: "#fef2f2", border: "#fca5a5", dot: "#dc2626" },
  given_away: { bg: "#eff6ff", border: "#93c5fd", dot: "#2563eb" },
};

const STATUS_LABELS: Record<string, string> = {
  ok:         "Healthy",
  warning:    "Needs Attention",
  unknown:    "No Sensor",
  dead:       "Dead",
  given_away: "Given Away",
};

/** Options shown in the "Change Status" dropdown (terminal statuses only) */
const STATUS_CHANGE_OPTIONS: { value: PlantStatus; label: string; emoji: string }[] = [
  { value: "dead",       label: "Mark as Dead",       emoji: "☠️" },
  { value: "given_away", label: "Mark as Given Away",  emoji: "🎁" },
];

function MetricBar({
  icon,
  label,
  value,
  min,
  max,
  unit,
  isOutOfRange,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | null;
  min: number;
  max: number;
  unit: string;
  isOutOfRange: boolean;
}) {
  if (value === null) return null;

  // Clamp value into [min-buffer, max+buffer] for display
  const buffer = (max - min) * 0.2;
  const low = min - buffer;
  const high = max + buffer;
  const pct = Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100));
  const minPct = ((min - low) / (high - low)) * 100;
  const maxPct = ((max - low) / (high - low)) * 100;

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, color: "#374151" }}>
          {icon}
          {label}
        </span>
        <span style={{ fontSize: 13, fontWeight: 600, color: isOutOfRange ? "#d97706" : "#111827" }}>
          {value.toFixed(1)} {unit}
          {isOutOfRange && " ⚠️"}
        </span>
      </div>
      <div style={{ position: "relative", height: 8, borderRadius: 4, background: "#e5e7eb", overflow: "visible" }}>
        {/* Acceptable range highlight */}
        <div
          style={{
            position: "absolute",
            left: `${minPct}%`,
            width: `${maxPct - minPct}%`,
            height: "100%",
            background: "#bbf7d0",
            borderRadius: 4,
          }}
        />
        {/* Current value indicator */}
        <div
          style={{
            position: "absolute",
            left: `${pct}%`,
            transform: "translateX(-50%)",
            top: -2,
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: isOutOfRange ? "#d97706" : "#16a34a",
            border: "2px solid white",
            boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
            zIndex: 1,
          }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#9ca3af", marginTop: 2 }}>
        <span>{min}{unit}</span>
        <span>{max}{unit}</span>
      </div>
    </div>
  );
}

export function PlantCard({ plant, onUpdate, onRemove }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [showSensorModal, setShowSensorModal] = useState(false);
  const [showStatusMenu, setShowStatusMenu] = useState(false);
  const [changingStatus, setChangingStatus] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const colors = STATUS_COLORS[plant.status] ?? STATUS_COLORS.unknown;
  const r = plant.last_readings;
  const cr = plant.care_ranges;

  async function handleRefresh() {
    setRefreshing(true);
    try {
      const updated = await api.refreshReadings(plant.plant_id);
      onUpdate(updated);
    } catch (e) {
      console.error(e);
    } finally {
      setRefreshing(false);
    }
  }

  async function handleRangesChange(ranges: CareRanges): Promise<void> {
    // Let errors propagate to CareRangesEditor so it can show them inline.
    const updated = await api.updateCareRanges(plant.plant_id, ranges);
    onUpdate(updated);
  }

  async function handleStatusChange(newStatus: PlantStatus) {
    setShowStatusMenu(false);
    const isTerminal = TERMINAL_STATUSES.has(newStatus);
    const label = STATUS_LABELS[newStatus] ?? newStatus;
    const confirmed = window.confirm(
      `Mark "${plant.name}" as ${label}?${
        isTerminal ? "\n\nThis will end the plant's workflow and remove it from the dashboard." : ""
      }`
    );
    if (!confirmed) return;

    setChangingStatus(true);
    try {
      await api.updateStatus(plant.plant_id, newStatus);
      if (isTerminal) {
        onRemove(plant.plant_id);
      }
    } catch (e) {
      console.error(e);
      alert("Failed to update plant status. Please try again.");
    } finally {
      setChangingStatus(false);
    }
  }

  const lastChecked = plant.last_checked_at
    ? new Date(plant.last_checked_at).toLocaleString()
    : "Never";

  return (
    <div
      style={{
        border: `1.5px solid ${colors.border}`,
        borderRadius: 12,
        background: colors.bg,
        padding: 16,
        boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: colors.dot,
                display: "inline-block",
                flexShrink: 0,
              }}
            />
            <span style={{ fontWeight: 700, fontSize: 16, color: "#111827" }}>{plant.name}</span>
            <span style={{ fontSize: 12, background: "#e5e7eb", borderRadius: 99, padding: "2px 8px", color: "#6b7280" }}>
              {STATUS_LABELS[plant.status]}
            </span>
          </div>
          <div style={{ fontSize: 13, color: "#6b7280", marginTop: 2, marginLeft: 18 }}>
            <em>{plant.species}</em>
          </div>
        </div>

        <div style={{ display: "flex", gap: 6 }}>
          {(plant.sensor_device_id || plant.sensor_entity_id) && (
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              title="Refresh now"
              style={{
                border: "1px solid #d1d5db",
                borderRadius: 6,
                padding: "4px 8px",
                background: "#fff",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 4,
                fontSize: 12,
                color: "#374151",
              }}
            >
              <RefreshCw size={13} style={{ animation: refreshing ? "spin 1s linear infinite" : undefined }} />
              {refreshing ? "…" : "Refresh"}
            </button>
          )}

          {/* Change Status menu */}
          <div ref={menuRef} style={{ position: "relative" }}>
            <button
              onClick={() => setShowStatusMenu((v) => !v)}
              disabled={changingStatus}
              title="Change plant status"
              style={{
                border: "1px solid #d1d5db",
                borderRadius: 6,
                padding: "4px 8px",
                background: "#fff",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                fontSize: 12,
                color: "#374151",
              }}
            >
              <MoreHorizontal size={14} />
            </button>
            {showStatusMenu && (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  right: 0,
                  background: "#fff",
                  border: "1px solid #e5e7eb",
                  borderRadius: 8,
                  boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
                  minWidth: 190,
                  zIndex: 100,
                  overflow: "hidden",
                }}
              >
                <div style={{ padding: "6px 12px", fontSize: 11, fontWeight: 600, color: "#9ca3af", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  Change Status
                </div>
                {STATUS_CHANGE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => handleStatusChange(opt.value)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      width: "100%",
                      padding: "8px 12px",
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      fontSize: 13,
                      color: "#374151",
                      textAlign: "left",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#f9fafb")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                  >
                    <span>{opt.emoji}</span>
                    <span>{opt.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <button
            onClick={() => setExpanded((v) => !v)}
            style={{
              border: "1px solid #d1d5db",
              borderRadius: 6,
              padding: "4px 8px",
              background: "#fff",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              fontSize: 12,
              color: "#374151",
            }}
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {/* Sensor badge */}
      <div style={{ marginTop: 8, marginLeft: 18, fontSize: 12, color: "#6b7280", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        {plant.sensor_device_name
          ? `🔌 ${plant.sensor_device_name}`
          : plant.sensor_entity_id
          ? `🔌 ${plant.sensor_entity_id}`
          : null}
        {plant.last_checked_at && (
          <span>· checked {lastChecked}</span>
        )}
        <button
          onClick={() => setShowSensorModal(true)}
          style={{
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
            fontSize: 12,
            color: plant.sensor_device_id || plant.sensor_entity_id ? "#6b7280" : "#d97706",
            textDecoration: "underline",
            textDecorationStyle: "dotted",
            flexShrink: 0,
          }}
        >
          {plant.sensor_device_id || plant.sensor_entity_id ? "Change sensor" : "⚠️ No sensor — Add one"}
        </button>
      </div>

      {/* Last-error notices — desired-vs-applied pattern */}
      {(plant.last_association_error ||
        plant.last_sensor_read_error ||
        plant.last_care_ranges_fetch_error ||
        plant.last_alert_error) && (
        <div style={{ marginTop: 8, marginLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
          {[
            { label: "Association", msg: plant.last_association_error },
            { label: "Sensor read", msg: plant.last_sensor_read_error },
            { label: "Care ranges", msg: plant.last_care_ranges_fetch_error },
            { label: "HA alert", msg: plant.last_alert_error },
          ]
            .filter((e) => e.msg)
            .map((e) => (
              <div
                key={e.label}
                title={e.msg ?? undefined}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 6,
                  fontSize: 11,
                  color: "#92400e",
                  background: "#fef3c7",
                  border: "1px solid #fcd34d",
                  borderRadius: 6,
                  padding: "3px 8px",
                  lineHeight: 1.4,
                }}
              >
                <span style={{ flexShrink: 0, fontWeight: 600 }}>⚠ {e.label}:</span>
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    maxWidth: 320,
                  }}
                >
                  {e.msg}
                </span>
              </div>
            ))}
        </div>
      )}

      {/* Live readings */}
      {r && (
        <div style={{ marginTop: 12 }}>
          <MetricBar
            icon={<Droplets size={13} />}
            label="Soil Moisture"
            value={r.soil_moisture}
            min={cr.soil_moisture_min}
            max={cr.soil_moisture_max}
            unit="%"
            isOutOfRange={plant.out_of_range_fields.includes("soil_moisture")}
          />
          <MetricBar
            icon={<Thermometer size={13} />}
            label="Temperature"
            value={r.temperature}
            min={cr.temperature_min}
            max={cr.temperature_max}
            unit="°F"
            isOutOfRange={plant.out_of_range_fields.includes("temperature")}
          />
          <MetricBar
            icon={<Wind size={13} />}
            label="Air Humidity"
            value={r.air_humidity}
            min={cr.air_humidity_min}
            max={cr.air_humidity_max}
            unit="%"
            isOutOfRange={plant.out_of_range_fields.includes("air_humidity")}
          />
          {r.light_lux !== null && cr.light_lux_min !== null && cr.light_lux_max !== null && (
            <MetricBar
              icon={<Sun size={13} />}
              label="Light"
              value={r.light_lux}
              min={cr.light_lux_min}
              max={cr.light_lux_max}
              unit=" lux"
              isOutOfRange={plant.out_of_range_fields.includes("light_lux")}
            />
          )}
        </div>
      )}

      {/* Expanded: care ranges editor */}
      {expanded && (
        <div
          style={{
            marginTop: 16,
            paddingTop: 16,
            borderTop: "1px solid #e5e7eb",
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 14, color: "#374151", marginBottom: 10 }}>
            Care Ranges
          </div>
          <CareRangesEditor
            ranges={plant.care_ranges}
            source={plant.care_ranges_source}
            onChange={handleRangesChange}
          />
        </div>
      )}
      {showSensorModal && (
        <AssociateSensorModal
          plant={plant}
          onClose={() => setShowSensorModal(false)}
          onUpdate={(updated) => {
            onUpdate(updated);
            setShowSensorModal(false);
          }}
        />
      )}
    </div>
  );
}
