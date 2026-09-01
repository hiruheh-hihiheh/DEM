import axios from 'axios'
import type { DamGeoJSON } from '../types/dam'

const api = axios.create({
  baseURL: 'http://127.0.0.1:8000/api',
  timeout: 15000,
})

export const fetchDams = async (): Promise<DamGeoJSON> => {
  const response = await api.get<DamGeoJSON>('/dams')
  return response.data
}

export default api