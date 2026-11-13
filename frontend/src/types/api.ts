export interface ApiError {
  code: string
  message: string
  detail?: string
  traceId?: string | null
  status?: number
  extra?: Record<string, unknown>
}

export interface ListPayload<T> {
  items: T[]
  total?: number
}
