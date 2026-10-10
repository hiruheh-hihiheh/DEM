export const uploadDem = async (file: File, siteSlug?: string, siteName?: string) => {
  const form = new FormData()
  form.append('file', file)
  if (siteSlug) form.append('site_slug', siteSlug)
  if (siteName) form.append('site_name', siteName)
  const res = await api.post('/sites/upload-dem', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  })
  return res.data
}

export const getSiteMeta = async (siteSlug: string) => {
  const res = await api.get(`/sites/${siteSlug}/validate`)
  return res.data
}

export const generatePreview = async (siteSlug: string, payload: any) => {
  const res = await api.post(`/sites/${siteSlug}/preview`, payload)
  return res.data
}

export const prepareSite = async (siteSlug: string, payload: any) => {
  const res = await api.post(`/sites/${siteSlug}/prepare`, payload)
  return res.data
}

export const runPipeline = async (siteSlug: string, dryRun = false) => {
  const res = await api.post(`/sites/${siteSlug}/run-pipeline`, null, { params: { dry_run: dryRun } })
  return res.data
}