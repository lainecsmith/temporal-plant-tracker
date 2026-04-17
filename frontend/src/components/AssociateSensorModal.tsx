import React, { useEffect, useState } from "react";
import { X, Loader } from "lucide-react";
import type { HADevice, PlantState } from "../types";
import { api } from "../api/client";

interface Props {
  plant: PlantState;
  onClose: () => void;
  onUpdate: (updated: PlantState) => void;
}

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

export function AssociateSensorModal({ plant, onClose, onUpdate }: Props) {
  const [devices, setDevices] = useState<HADevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>(
    plant.sensor_device_id ?? ""
  );
  const [loadingDevices, setLoadingDevices] = useState(true);
  const [associating, setAssociating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listDevices()
      .then(setDevices)
      .catch(() => setDevices([]))
      .finally(() => setLoadingDevices(false));
  }, []);

  async function handleAssociate() {
    if (!selectedDeviceId) return;
    const selectedDevice = devices.find((d) => d.device_id === selectedDeviceId);
    if (!selectedDevice) return;

    setAssociating(true);
    setError(null);
    try {
      const sensorEntities = buildSensorEntities(selectedDevice);
      const updated = await api.associateDevice(
        plant.plant_id,
        selectedDevice.device_id,
        selectedDevice.name,
        sensorEntities
      );
      onUpdate(updated);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to associate sensor");
    } finally {
      setAssociating(false);
    }
  }

  const selectedDevice = devices.find((d) => d.device_id === selectedDeviceId) ?? null;
  const isReplacing = !!plant.sensor_device_id || !!plant.sensor_entity_id;

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

        <h2 style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 700 }}>
          {isReplacing ? "Change Sensor" : "Associate a Sensor"}
        </h2>
        <p style={{ margin: "0 0 20px", fontSize: 14, color: "#6b7280" }}>
          {isReplacing
            ? <>Pick a different sensor for <strong>{plant.name}</strong>. The current sensor will be replaced.</>
            : <>Pick a Zigbee plant sensor from Home Assistant to monitor <strong>{plant.name}</strong>.</>
          }
        </p>

        {/* Current sensor badge (if replacing) */}
        {isReplacing && (
          <div
            style={{
              background: "#f8fafc",
              border: "1px solid #e5e7eb",
              borderRadius: 8,
              padding: "8px 12px",
              marginBottom: 16,
              fontSize: 13,
              color: "#6b7280",
            }}
          >
            Currently: 🔌{" "}
            <span style={{ color: "#374151", fontWeight: 500 }}>
              {plant.sensor_device_name ?? plant.sensor_entity_id}
            </span>
          </div>
        )}

        {error && (
          <div
            style={{
              background: "#fef2f2", border: "1px solid #fca5a5",
              borderRadius: 8, padding: "8px 12px", marginBottom: 16,
              fontSize: 13, color: "#dc2626",
            }}
          >
            {error}
          </div>
        )}

        {/* Device list */}
        {loadingDevices ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#6b7280", fontSize: 14, margin: "20px 0" }}>
            <Loader size={16} style={{ animation: "spin 1s linear infinite" }} />
            Loading sensors…
          </div>
        ) : devices.length === 0 ? (
          <div
            style={{
              background: "#fefce8", border: "1px solid #fde68a",
              borderRadius: 8, padding: "12px", fontSize: 13, color: "#92400e", marginBottom: 16,
            }}
          >
            No plant sensor devices found in Home Assistant. Make sure your sensors are set up in HA.
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
                        <span style={{ fontSize: 12, color: "#6b7280" }}>in {device.area_name}</span>
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

        {/* Entity preview */}
        {selectedDevice && (
          <div
            style={{
              marginBottom: 16, padding: "10px 12px",
              background: "#f9fafb", borderRadius: 8, border: "1px solid #e5e7eb",
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
              Entities that will be tracked:
            </div>
            {selectedDevice.entities.map((entity) => (
              <div
                key={entity.entity_id}
                style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#6b7280", paddingBottom: 3 }}
              >
                <span>{entity.friendly_name}</span>
                <span style={{ fontFamily: "monospace", color: "#9ca3af" }}>{entity.entity_id}</span>
              </div>
            ))}
          </div>
        )}

        {/* Actions */}
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={handleAssociate}
            disabled={associating || !selectedDeviceId}
            style={primaryBtn(associating || !selectedDeviceId)}
          >
            {associating
              ? <><Loader size={13} style={{ marginRight: 6, animation: "spin 1s linear infinite" }} />Saving…</>
              : isReplacing ? "Change Sensor" : "Associate Sensor"
            }
          </button>
          <button onClick={onClose} style={secondaryBtn}>
            Cancel
          </button>
        </div>
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

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
