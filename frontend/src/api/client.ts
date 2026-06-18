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
    const detail = await res.text();
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ---- Plants ---------------------------------------------------------------

export const api = {
  listPlants: (): Promise<PlantState[]> =>
    request("/plants"),

  getPlant: (plantId: string): Promise<PlantState> =>
    request(`/plants/${plantId}`),

  createPlant: (name: string, species: string): Promise<PlantState> =>
    request("/plants", {
      method: "POST",
      body: JSON.stringify({ name, species }),
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

  /** Update the lifecycle status of a plant (e.g. "dead", "given_away") */
  updateStatus: (plantId: string, status: PlantStatus): Promise<PlantState> =>
    request(`/plants/${plantId}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),

  // ---- Sensors / Devices --------------------------------------------------

  /** Legacy entity-level list */
  listSensors: (): Promise<HASensor[]> => request("/sensors"),

  /** Device-level list (preferred) */
  listDevices: (): Promise<HADevice[]> => request("/devices"),
};
