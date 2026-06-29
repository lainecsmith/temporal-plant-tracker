import type { CareRanges, HADevice, HASensor, PlantState, PlantStatus } from "../types";

const BASE = "/api";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    // FastAPI error responses are JSON: { "detail": "..." }
    // Extract the detail string for clean error messages (e.g. validation rejections).
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed?.detail === "string") detail = parsed.detail;
    } catch {
      // Not JSON — use raw text as-is
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ---- Plants ---------------------------------------------------------------

export const api = {
  listPlants: (): Promise<PlantState[]> =>
    request("/plants"),

  getPlant: (plantId: string): Promise<PlantState> =>
    request(`/plants/${plantId}`),

  createPlant: (name: string, species: string, room?: string | null): Promise<PlantState> =>
    request("/plants", {
      method: "POST",
      body: JSON.stringify({ name, species, room: room ?? null }),
    }),

  updateCareRanges: (
    plantId: string,
    care_ranges: CareRanges
  ): Promise<PlantState> =>
    request(`/plants/${plantId}/care-ranges`, {
      method: "PUT",
      body: JSON.stringify({ care_ranges }),
    }),

  /** Legacy: associate by a single entity ID */
  associateSensor: (
    plantId: string,
    sensor_entity_id: string
  ): Promise<PlantState> =>
    request(`/plants/${plantId}/sensor`, {
      method: "POST",
      body: JSON.stringify({ sensor_entity_id }),
    }),

  /** Associate a full HA device (with its entity map) */
  associateDevice: (
    plantId: string,
    device_id: string,
    device_name: string,
    sensor_entities: Record<string, string>
  ): Promise<PlantState> =>
    request(`/plants/${plantId}/device`, {
      method: "POST",
      body: JSON.stringify({ device_id, device_name, sensor_entities }),
    }),

  refreshReadings: (plantId: string): Promise<PlantState> =>
    request(`/plants/${plantId}/refresh`, { method: "POST" }),

  /** Record that a plant was watered. Pass a Date to backfill; omit for right now. */
  logWatering: (plantId: string, wateredAt?: Date): Promise<PlantState> =>
    request(`/plants/${plantId}/water`, {
      method: "POST",
      body: wateredAt ? JSON.stringify({ watered_at: wateredAt.toISOString() }) : undefined,
    }),

  /** Update the lifecycle status of a plant (e.g. "dead", "given_away") */
  updateStatus: (plantId: string, status: PlantStatus): Promise<PlantState> =>
    request(`/plants/${plantId}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),

  /** Move a plant to a room, or clear its room assignment (pass null to unassign) */
  updateRoom: (plantId: string, room: string | null): Promise<PlantState> =>
    request(`/plants/${plantId}/room`, {
      method: "PUT",
      body: JSON.stringify({ room }),
    }),

  // ---- Sensors / Devices --------------------------------------------------

  /** Legacy entity-level list */
  listSensors: (): Promise<HASensor[]> => request("/sensors"),

  /** Device-level list (preferred) */
  listDevices: (): Promise<HADevice[]> => request("/devices"),
};
