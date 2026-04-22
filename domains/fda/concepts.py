"""FDA regulatory concepts reference — embedded in the planner prompt.

Provides definitions and taxonomy so the planner creates correct research plans
for medical device regulatory questions.
"""

FDA_CONCEPTS = """\
REGULATORY PATHWAYS:
- 510(k) (Premarket Notification): Device is "substantially equivalent" to a legally marketed predicate device. Most common pathway for Class II devices. Results in "clearance" (NOT "approval").
- PMA (Premarket Approval): Required for Class III (high-risk) devices. Requires clinical data proving safety and effectiveness. Results in "approval."
- De Novo: For novel low-to-moderate risk devices with no predicate. Creates a new regulatory classification.
- Exempt: Low-risk Class I devices exempt from premarket review. Still subject to general controls.
- HDE (Humanitarian Device Exemption): For devices treating conditions affecting <8,000 patients/year.

DEVICE CLASSES:
- Class I: Low risk. General controls only. Most are exempt from 510(k). Examples: bandages, tongue depressors.
- Class II: Moderate risk. General + special controls. Most require 510(k). Examples: powered wheelchairs, infusion pumps, surgical drapes.
- Class III: High risk. General controls + PMA required. Examples: heart valves, implantable defibrillators, breast implants.

KEY IDENTIFIERS:
- K-number: 510(k) submission identifier. Format: K + 6 digits (e.g., K213456).
- P-number: PMA identifier. Format: P + 6 digits (e.g., P160054).
- Product Code: 3-letter code identifying a device type (e.g., DRG = physiological signal transmitters). ~6,000 product codes exist.
- Regulation Number: CFR reference (e.g., 870.2910 = 21 CFR 870.2910).

SUBSTANTIAL EQUIVALENCE:
A 510(k) must demonstrate the new device is "substantially equivalent" to a predicate device — same intended use AND either same technological characteristics OR different characteristics that don't raise new safety/effectiveness questions. The predicate device is identified by its K-number.

MAUDE (Manufacturer and User Facility Device Experience):
- Mandatory adverse event reporting database
- Event types: Death, Injury, Malfunction
- Reports filed by manufacturers (mandatory), user facilities (mandatory for deaths/serious injuries), and voluntary reporters
- Contains ~24 million reports total
- Each report includes: device info, event narrative, patient problems, manufacturer investigation

RECALLS:
- Class I: Reasonable probability of serious adverse health consequences or death
- Class II: May cause temporary or medically reversible adverse health consequences
- Class III: Not likely to cause adverse health consequences
- Initiated by manufacturer or ordered by FDA

PRODUCT CODE TAXONOMY:
Product codes group devices by intended use and technology. Each code maps to:
- A device class (I, II, or III)
- A regulation number (21 CFR section)
- A submission type (510(k), PMA, De Novo, or Exempt)
- A medical specialty panel (e.g., CV = Cardiovascular, OR = Orthopedic)

CLEARANCE TIMELINE:
- Date Received: when FDA accepts the 510(k) submission
- Decision Date: when FDA issues the clearance decision
- Clearance Time = Decision Date - Date Received (typically 60-180 days for 510(k))
- Expedited review and third-party review can shorten timelines"""
