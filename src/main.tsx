import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import OpportunityRadar, { type RadarData } from "@/components/radar/opportunity-radar"
import radarData from "../data/opportunities.json"
import "./styles.css"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <OpportunityRadar data={radarData as RadarData} />
  </StrictMode>,
)
