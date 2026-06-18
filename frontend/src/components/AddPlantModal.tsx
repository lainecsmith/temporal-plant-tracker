import React, { useEffect, useRef, useState } from "react";
import { X, Loader } from "lucide-react";
import type { CareRanges, HADevice, PlantState } from "../types";
import { CareRangesEditor } from "./CareRangesEditor";
import { api } from "../api/client";

interface Props {
  onClose: () => void;
  onCreated: (plant: PlantState) => void;
}

type Step = "name" | "ranges" | "sensor" | "done";

const SOURCE_BANNERS: Record<string, { emoji: string; text: string; bg: string }> = {
  openplantbook: { emoji: "📖", text: "Found in OpenPlantbook — ranges pre-filled!", bg: "#f0fdf4" },
  ai:            { emoji: "🤖", text: "Not in OpenPlantbook — ranges suggested by AI.", bg: "#eff6ff" },
  manual:        { emoji: "✏️", text: "Manually set.", bg: "#fafaf9" },
  unknown:       { emoji: "⏳", text: "Looking up care ranges…", bg: "#fafaf9" },
};

// Human-readable labels and icons for HA device_class values
const DEVICE_CLASS_META: Record<string, { label: string; icon: string }> = {
  moisture:    { label: "Moisture",    icon: "💧" },
  temperature: { label: "Temperature", icon: "🌡️" },
  humidity:    { label: "Humidity",    icon: "💦" },
  illuminance: { label: "Light",       icon: "☀️" },
  battery:     { label: "Battery",     icon: "🔋" },
};

/** Build a device_class → entity_id map from a device's entities */
function buildSensorEntities(device: HADevice): Record<string, string> {
  const map: Record<string, string> = {};
  for (const entity of device.entities) {
    if (entity.device_class) {
      map[entity.device_class] = entity.entity_id;
    }
  }
  return map;
}

export function AddPlantModal({ onClose, onCreated }: Props) {
  const [step, setStep] = useState<Step>("name");
  const [name, setName] = useState("");
  const [species, setSpecies] = useState("");
  const [plant, setPlant] = useState<PlantState | null>(null);
  const [devices, setDevices] = useState<HADevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>("");
  const [loadingDevices, setLoadingDevices] = useState(false);
  const [creating, setCreating] = useState(false);
  // rangesNotFound = poll gave up (workflow took > 60 s); user must enter manually
  const [rangesNotFound, setRangesNotFound] = useState(false);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [savingRanges, setSavingRanges] = useState(false);
  const [associating, setAssociating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 1 → 2: create plant & poll until care ranges are ready
  async function handleCreate() {
    if (!name.trim() || !species.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api.createPlant(name.trim(), species.trim());
      setPlant(created);
      setStep("ranges");
      // If ranges aren't loaded yet, poll until they are
      if (created.care_ranges_source === "unknown") {
        pollForRanges(created.plant_id);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create plant");
    } finally {
      setCreating(false);
    }
  }

  // Clean up any in-flight poll timer when the modal unmounts
  useEffect(() => {
    return () => {
      if (pollTimerRef.current !== null) clearTimeout(pollTimerRef.current);
    };
  }, []);

  function pollForRanges(plantId: string) {
    let attempts = 0;
    const maxAttempts = 30; // 30 × 2 s = up to 60 s

    // Use sequential setTimeout (not setInterval) so polls never overlap when
    // the API is slow — each new check fires only after the previous one lands.
    async function tryPoll() {
      if (attempts >= maxAttempts) {
        setRangesNotFound(true);
        return;
      }
      attempts++;
      try {
        const updated = await api.getPlant(plantId);
        if (updated.care_ranges_source !== "unknown") {
          // Ranges are ready — updating plant.care_ranges_source drives the UI
          setPlant(updated);
          return; // stop polling
        }
      } catch (_) {
        // ignore transient errors; keep retrying
      }
      // Schedule next attempt only after this one finished
      pollTimerRef.current = setTimeout(tryPoll, 2000);
    }

    // Kick off the first check after a short delay so the workflow has a
    // moment to start, keeping the initial wait to ~500 ms instead of 2 s.
    pollTimerRef.current = setTimeout(tryPoll, 500);
  }

  // Step 2 → 3: save edited ranges (only if the user changed them) & load devices
  async function handleRangesConfirm(ranges: CareRanges) {
    if (!plant) return;

    // Only send the update signal when the user actually edited the ranges.
    // If the source is still "openplantbook" or "ai" the ranges are unchanged
    // and calling updateCareRanges would overwrite the source with "manual".
    if (plant.care_ranges_source === "manual") {
      setSavingRanges(true);
      setError(null);
      try {
        const updated = await api.updateCareRanges(plant.plant_id, ranges);
        setPlant(updated);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to save ranges");
        setSavingRanges(false);
        return;
      } finally {
        setSavingRanges(false);
      }
    }

    await loadDevices();
    setStep("sensor");
  }

  async function loadDevices() {
    setLoadingDevices(true);
    try {
      const list = await api.listDevices();
      setDevices(list);
    } catch (_) {
      setDevices([]);
    } finally {
      setLoadingDevices(false);
    }
  }

  // Step 3 → done: associate device
  async function handleAssociateDevice() {
    if (!plant) return;
    setAssociating(true);
    setError(null);
    let finalPlant = plant;
    try {
      if (selectedDeviceId) {
        const selectedDevice = devices.find((d) => d.device_id === selectedDeviceId);
        if (selectedDevice) {
          const sensorEntities = buildSensorEntities(selectedDevice);
          const updated = await api.associateDevice(
            plant.plant_id,
            selectedDevice.device_id,
            selectedDevice.name,
            sensorEntities,
          );
          setPlant(updated);
          finalPlant = updated;
        }
      }
      onCreated(finalPlant);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to associate sensor");
    } finally {
      setAssociating(false);
    }
  }

  function handleSkipSensor() {
    if (plant) onCreated(plant);
    onClose();
  }

  const selectedDevice = devices.find((d) => d.device_id === selectedDeviceId) ?? null;

  return (
    <div
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 100,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          background: "#fff",
          borderRadius: 14,
          padding: 28,
          width: "100%",
          maxWidth: 520,
          boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
          position: "relative",
          maxHeight: "90vh",
          overflowY: "auto",
        }}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            position: "absolute", top: 14, right: 14,
            background: "none", border: "none", cursor: "pointer",
            color: "#9ca3af",
          }}
        >
          <X size={20} />
        </button>

        {/* Step indicator */}
        <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
          {(["name", "ranges", "sensor"] as Step[]).map((s, i) => (
            <div key={s} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div
                style={{
                  width: 24, height: 24, borderRadius: "50%",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 12, fontWeight: 700,
                  background: step === s ? "#16a34a" : (["name","ranges","sensor"].indexOf(step) > i ? "#bbf7d0" : "#e5e7eb"),
                  color: step === s ? "#fff" : "#374151",
                }}
              >
                {i + 1}
              </div>
              {i < 2 && <div style={{ width: 24, height: 2, background: "#e5e7eb" }} />}
            </div>
          ))}
        </div>

        {error && (
          <div style={{ background: "#fef2f2", border: "1px solid #fca5a5", borderRadius: 8, padding: "8px 12px", marginBottom: 16, fontSize: 13, color: "#dc2626" }}>
            {error}
          </div>
        )}

        {/* ── Step 1: Name & Species ── */}
        {step === "name" && (
          <>
            <h2 style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 700 }}>Add a Plant</h2>
            <p style={{ margin: "0 0 20px", fontSize: 14, color: "#6b7280" }}>
              Enter the name you'd like to give your plant and its species. We'll look up care ranges automatically.
            </p>

            <label style={labelStyle}>Plant Name</label>
            <input
              autoFocus
              style={fieldStyle}
              placeholder="e.g. Living Room Monstera"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />

            <label style={labelStyle}>Species / Common Name</label>
            <input
              style={fieldStyle}
              placeholder="e.g. Monstera deliciosa"
              value={species}
              onChange={(e) => setSpecies(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />

            <button
              onClick={handleCreate}
              disabled={creating || !name.trim() || !species.trim()}
              style={primaryBtn(creating || !name.trim() || !species.trim())}
            >
              {creating ? <><Loader size={14} style={{ marginRight: 6 }} />Creating…</> : "Continue →"}
            </button>
          </>
        )}

        {/* ── Step 2: Review / Edit Care Ranges ── */}
        {step === "ranges" && plant && (
          <>
            <h2 style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 700 }}>Care Ranges</h2>

            {/* Source banner */}
            {(() => {
              const banner = SOURCE_BANNERS[plant.care_ranges_source] ?? SOURCE_BANNERS.unknown;
              return (
                <div style={{ background: banner.bg, border: "1px solid #e5e7eb", borderRadius: 8, padding: "8px 12px", marginBottom: 16, fontSize: 13 }}>
                  {banner.emoji} {banner.text}
                </div>
              );
            })()}

            {/* Show spinner while the workflow activity is still running.
                Switch to editor only once care_ranges_source is known,
                or if the poll gave up (rangesNotFound). This guarantees the
                editor never renders with stale default values. */}
            {plant.care_ranges_source === "unknown" && !rangesNotFound ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#6b7280", fontSize: 14, margin: "20px 0" }}>
                <Loader size={16} style={{ animation: "spin 1s linear infinite" }} />
                Looking up care ranges for <strong>{plant.species}</strong>…
              </div>
            ) : (
              <>
                {rangesNotFound && plant.care_ranges_source === "unknown" && (
                  <div style={{ background: "#fefce8", border: "1px solid #fde68a", borderRadius: 8, padding: "10px 12px", marginBottom: 12, fontSize: 13, color: "#92400e" }}>
                    ⚠️ Couldn't look up ranges automatically — please enter them manually below.
                  </div>
                )}
                <CareRangesEditor
                  ranges={plant.care_ranges}
                  source={plant.care_ranges_source}
                  reasoning={plant.care_ranges_source === "ai" ? (plant.care_ranges_reasoning ?? undefined) : undefined}
                  onChange={(ranges) =>
                    // Propagate user edits back up so "These look good →" sends
                    // the edited values (not the original fetched ones).
                    setPlant((prev) =>
                      prev ? { ...prev, care_ranges: ranges, care_ranges_source: "manual" } : null
                    )
                  }
                  readOnly={false}
                />
                <div style={{ display: "flex", gap: 8, marginTop: 20 }}>
                  <button
                    onClick={() => handleRangesConfirm(plant.care_ranges)}
                    disabled={savingRanges}
                    style={primaryBtn(savingRanges)}
                  >
                    {savingRanges ? "Saving…" : "These look good →"}
                  </button>
                </div>
              </>
            )}
          </>
        )}

        {/* ── Step 3: Select Sensor Device ── */}
        {step === "sensor" && plant && (
          <>
            <h2 style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 700 }}>Associate a Sensor</h2>
            <p style={{ margin: "0 0 16px", fontSize: 14, color: "#6b7280" }}>
              Pick a Zigbee plant sensor from Home Assistant to monitor <strong>{plant.name}</strong>.
              You can skip this for now and add it later.
            </p>

            {loadingDevices ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#6b7280", fontSize: 14 }}>
                <Loader size={16} style={{ animation: "spin 1s linear infinite" }} />
                Loading sensors…
              </div>
            ) : devices.length === 0 ? (
              <div style={{ background: "#fefce8", border: "1px solid #fde68a", borderRadius: 8, padding: "12px", fontSize: 13, color: "#92400e", marginBottom: 16 }}>
                No plant sensor devices found in Home Assistant. Make sure your sensors are set up, or skip and add later.
              </div>
            ) : (
              <div style={{ marginBottom: 16 }}>
                {devices.map((device) => {
                  const isSelected = selectedDeviceId === device.device_id;
                  return (
                    <label
                      key={device.device_id}
                      style={{
                        display: "flex", alignItems: "flex-start", gap: 12,
                        padding: "12px 14px", borderRadius: 10, marginBottom: 8,
                        border: `1.5px solid ${isSelected ? "#16a34a" : "#e5e7eb"}`,
                        background: isSelected ? "#f0fdf4" : "#fff",
                        cursor: "pointer",
                        transition: "border-color 0.15s, background 0.15s",
                      }}
                    >
                      <input
                        type="radio"
                        name="device"
                        value={device.device_id}
                        checked={isSelected}
                        onChange={() => setSelectedDeviceId(device.device_id)}
                        style={{ marginTop: 3, accentColor: "#16a34a", flexShrink: 0 }}
                      />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        {/* Device name + area */}
                        <div style={{ display: "flex", alignItems: "baseline", gap: 6, flexWrap: "wrap" }}>
                          <span style={{ fontWeight: 700, fontSize: 14 }}>{device.name}</span>
                          {device.area_name && (
                            <span style={{ fontSize: 12, color: "#6b7280" }}>
                              in {device.area_name}
                            </span>
                          )}
                        </div>

                        {/* Manufacturer / model */}
                        {(device.manufacturer || device.model) && (
                          <div style={{ fontSize: 12, color: "#9ca3af", marginTop: 1 }}>
                            {[device.manufacturer, device.model].filter(Boolean).join(" · ")}
                          </div>
                        )}

                        {/* Entity type pills */}
                        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 7 }}>
                          {device.entities.map((entity) => {
                            const meta = DEVICE_CLASS_META[entity.device_class ?? ""] ?? {
                              label: entity.device_class ?? "Unknown",
                              icon: "📡",
                            };
                            return (
                              <span
                                key={entity.entity_id}
                                style={{
                                  display: "inline-flex", alignItems: "center", gap: 3,
                                  fontSize: 11, fontWeight: 500,
                                  padding: "2px 7px", borderRadius: 99,
                                  background: isSelected ? "#dcfce7" : "#f3f4f6",
                                  color: isSelected ? "#15803d" : "#374151",
                                  border: `1px solid ${isSelected ? "#86efac" : "#e5e7eb"}`,
                                }}
                              >
                                {meta.icon} {meta.label}
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    </label>
                  );
                })}
              </div>
            )}

            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={handleAssociateDevice}
                disabled={associating || !selectedDeviceId}
                style={primaryBtn(associating || !selectedDeviceId)}
              >
                {associating ? "Saving…" : "Associate Sensor"}
              </button>
              <button
                onClick={handleSkipSensor}
                style={secondaryBtn}
              >
                Skip for now
              </button>
            </div>

            {/* Preview of which entities will be tracked */}
            {selectedDevice && (
              <div style={{ marginTop: 16, padding: "10px 12px", background: "#f9fafb", borderRadius: 8, border: "1px solid #e5e7eb" }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                  Entities that will be tracked:
                </div>
                {selectedDevice.entities.map((entity) => (
                  <div key={entity.entity_id} style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#6b7280", paddingBottom: 3 }}>
                    <span>{entity.friendly_name}</span>
                    <span style={{ fontFamily: "monospace", color: "#9ca3af" }}>{entity.entity_id}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 13,
  fontWeight: 600,
  color: "#374151",
  marginBottom: 4,
  marginTop: 12,
};

const fieldStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 12px",
  border: "1px solid #d1d5db",
  borderRadius: 8,
  fontSize: 14,
  marginBottom: 4,
  outline: "none",
};

function primaryBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: "9px 18px",
    borderRadius: 8,
    border: "none",
    background: disabled ? "#d1d5db" : "#16a34a",
    color: "#fff",
    fontWeight: 600,
    fontSize: 14,
    cursor: disabled ? "not-allowed" : "pointer",
    display: "flex",
    alignItems: "center",
  };
}

const secondaryBtn: React.CSSProperties = {
  padding: "9px 18px",
  borderRadius: 8,
  border: "1px solid #d1d5db",
  background: "#fff",
  color: "#374151",
  fontWeight: 500,
  fontSize: 14,
  cursor: "pointer",
};
