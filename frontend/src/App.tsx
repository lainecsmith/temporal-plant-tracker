import React, { useCallback, useEffect, useState } from "react";
import { Leaf, Plus, RefreshCw } from "lucide-react";
import type { PlantState } from "./types";
import { api } from "./api/client";
import { PlantCard } from "./components/PlantCard";
import { AddPlantModal } from "./components/AddPlantModal";

export default function App() {
  const [plants, setPlants] = useState<PlantState[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPlants = useCallback(async () => {
    try {
      const list = await api.listPlants();
      setPlants(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load plants");
    }
  }, []);

  useEffect(() => {
    loadPlants().finally(() => setLoading(false));

    // Auto-refresh every 2 minutes so readings stay current in the UI
    const interval = setInterval(loadPlants, 120_000);
    return () => clearInterval(interval);
  }, [loadPlants]);

  async function handleManualRefresh() {
    setRefreshing(true);
    await loadPlants();
    setRefreshing(false);
  }

  function handlePlantUpdate(updated: PlantState) {
    setPlants((prev) =>
      prev.map((p) => (p.plant_id === updated.plant_id ? updated : p))
    );
  }

  function handlePlantRemove(plantId: string) {
    setPlants((prev) => prev.filter((p) => p.plant_id !== plantId));
  }

  function handlePlantCreated(plant: PlantState) {
    setPlants((prev) => {
      const exists = prev.some((p) => p.plant_id === plant.plant_id);
      // Always update in case the plant was already in the list with stale data
      // (e.g. loadPlants fired while the modal was still open)
      return exists
        ? prev.map((p) => (p.plant_id === plant.plant_id ? plant : p))
        : [...prev, plant];
    });
  }

  // Poll every 3 s for any plant whose care ranges haven't been fetched yet.
  // This covers the case where loadPlants() ran before the workflow activity
  // completed, leaving the card showing "unknown" ranges until the next 2-min
  // interval — now it self-corrects as soon as the workflow finishes.
  useEffect(() => {
    const unknownIds = plants
      .filter((p) => p.care_ranges_source === "unknown")
      .map((p) => p.plant_id);
    if (unknownIds.length === 0) return;

    const tick = async () => {
      for (const plantId of unknownIds) {
        try {
          const updated = await api.getPlant(plantId);
          if (updated.care_ranges_source !== "unknown") {
            setPlants((prev) =>
              prev.map((p) => (p.plant_id === plantId ? updated : p))
            );
          }
        } catch (_) {
          // ignore transient errors while polling
        }
      }
    };

    const timer = setInterval(tick, 3000);
    return () => clearInterval(timer);
  }, [plants]);

  const warningCount = plants.filter((p) => p.status === "warning").length;
  const okCount = plants.filter((p) => p.status === "ok").length;
  const unknownCount = plants.filter((p) => p.status === "unknown").length;

  return (
    <div style={{ minHeight: "100vh", background: "#f1f5f0", fontFamily: "system-ui, -apple-system, sans-serif" }}>
      {/* Header */}
      <header
        style={{
          background: "#fff",
          borderBottom: "1px solid #e5e7eb",
          padding: "14px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          position: "sticky",
          top: 0,
          zIndex: 50,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Leaf size={22} color="#16a34a" />
          <span style={{ fontWeight: 700, fontSize: 18, color: "#111827" }}>Plant Tracker</span>
          {plants.length > 0 && (
            <div style={{ display: "flex", gap: 6, marginLeft: 12 }}>
              {warningCount > 0 && (
                <span style={badge("#fffbeb", "#d97706")}>⚠️ {warningCount} need attention</span>
              )}
              {okCount > 0 && (
                <span style={badge("#f0fdf4", "#16a34a")}>✅ {okCount} healthy</span>
              )}
              {unknownCount > 0 && (
                <span style={badge("#f8fafc", "#6b7280")}>⏳ {unknownCount} no sensor</span>
              )}
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={handleManualRefresh}
            disabled={refreshing}
            style={{
              border: "1px solid #d1d5db",
              borderRadius: 8,
              padding: "7px 14px",
              background: "#fff",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 13,
              color: "#374151",
            }}
          >
            <RefreshCw size={14} style={{ animation: refreshing ? "spin 1s linear infinite" : undefined }} />
            Refresh
          </button>
          <button
            onClick={() => setShowAdd(true)}
            style={{
              border: "none",
              borderRadius: 8,
              padding: "7px 16px",
              background: "#16a34a",
              color: "#fff",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            <Plus size={15} />
            Add Plant
          </button>
        </div>
      </header>

      {/* Main content */}
      <main style={{ maxWidth: 900, margin: "0 auto", padding: "24px 16px" }}>
        {error && (
          <div
            style={{
              background: "#fef2f2",
              border: "1px solid #fca5a5",
              borderRadius: 10,
              padding: "12px 16px",
              marginBottom: 20,
              fontSize: 14,
              color: "#dc2626",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>⚠️ {error}</span>
            <button
              onClick={() => setError(null)}
              style={{ background: "none", border: "none", cursor: "pointer", color: "#dc2626" }}
            >
              ✕
            </button>
          </div>
        )}

        {loading ? (
          <div style={{ textAlign: "center", padding: "60px 0", color: "#6b7280" }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>🌿</div>
            <div>Loading your plants…</div>
          </div>
        ) : plants.length === 0 ? (
          <div
            style={{
              textAlign: "center",
              padding: "60px 24px",
              background: "#fff",
              borderRadius: 14,
              border: "1.5px dashed #d1d5db",
            }}
          >
            <div style={{ fontSize: 48, marginBottom: 12 }}>🪴</div>
            <h2 style={{ margin: "0 0 8px", color: "#111827", fontSize: 18 }}>No plants yet</h2>
            <p style={{ color: "#6b7280", marginBottom: 20, fontSize: 14 }}>
              Add your first plant to start tracking its health with Zigbee sensors.
            </p>
            <button
              onClick={() => setShowAdd(true)}
              style={{
                border: "none",
                borderRadius: 8,
                padding: "10px 20px",
                background: "#16a34a",
                color: "#fff",
                cursor: "pointer",
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              + Add Your First Plant
            </button>
          </div>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))",
              gap: 16,
            }}
          >
            {plants.map((plant) => (
              <PlantCard
                key={plant.plant_id}
                plant={plant}
                onUpdate={handlePlantUpdate}
                onRemove={handlePlantRemove}
              />
            ))}
          </div>
        )}
      </main>

      {showAdd && (
        <AddPlantModal
          onClose={() => setShowAdd(false)}
          onCreated={handlePlantCreated}
        />
      )}

      <style>{`
        * { box-sizing: border-box; }
        body { margin: 0; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

function badge(bg: string, color: string): React.CSSProperties {
  return {
    background: bg,
    color,
    fontSize: 12,
    fontWeight: 500,
    borderRadius: 99,
    padding: "2px 10px",
    border: `1px solid ${color}33`,
  };
}
