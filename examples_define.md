# UCKS Define Examples

Example prompts for the **Define** tab — describing civil engineering concepts
in natural language so the system structures them into UCKS entities.

---

## Building Sector

### Railing
Define a railing. It's a barrier along edges of stairs, balconies, or walkways
for fall protection. It has height, length, material, and can be a handrail,
guardrail, or balustrade. It can include bicycle railing features. It connects
to stairs, slabs, or walkways. It may be MASH-compliant for highway use.
Include surface texture and section type properties.

### Curtain Wall Panel
A curtain wall panel is a non-structural facade element that provides weather
protection. It has width, height, thickness, glazing type (single, double,
triple), U-value for thermal performance, and solar heat gain coefficient.
It is part of a curtain wall system and connects to mullions and transoms.

### Precast Concrete Girder
Define a precast concrete girder. It's a structural beam manufactured off-site
and assembled on location. It has span length, depth, width, cross-section
shape (I-beam, T-beam, box, inverted-T), concrete strength class (C30/37,
C40/50, C50/60), prestress type (pre-tensioned or post-tensioned), camber,
and self-weight. It supports slabs and connects to columns or bearing pads.
Include load-bearing capacity in kN.

---

## Infrastructure Sector

### Bridge Pier
A bridge pier is a vertical support structure that transfers deck loads to
the foundation. It has height, cross-section shape (circular, rectangular,
or hammerhead), number of columns, diameter or width, design load capacity
in kN, and material type (reinforced concrete or steel). It supports bridge
decks and is founded on pile caps or spread footings.

### Road Segment
Define a road segment. It's a continuous section of roadway between two
nodes (intersections or reference points). It has length, number of lanes,
lane width, surface type (asphalt, concrete, gravel), design speed in km/h,
pavement thickness, and road class (highway, arterial, collector, local).
It connects to intersections and may include shoulders and medians.

### Tunnel Lining
A tunnel lining is the structural shell that supports the excavated space.
It has inner diameter, thickness, segment length, material (reinforced
concrete, steel, shotcrete), number of segments per ring, waterproofing
type, and design life in years. It encloses the tunnel bore and connects
to adjacent lining rings. It resists soil and water pressure.

### Retaining Wall
Define a retaining wall. It holds back soil or rock from a surface. It has
height, base width, top width, batter angle, material (reinforced concrete,
masonry, gabion), and wall type (cantilever, gravity, anchored, mechanically
stabilized earth). It retains a soil mass and is founded on a foundation.
Include surcharge load capacity and drainage type.

---

## Facility Sector

### Air Handling Unit
An air handling unit is HVAC equipment that conditions and circulates air.
It has airflow rate in m3/h, cooling capacity in kW, heating capacity in kW,
filter type (G4, F7, F9, H13, H14), fan power consumption in kW, and noise
level in dB. It serves one or more spaces and connects to supply and return
duct segments. Include maintenance interval in days and commissioning date.

### Fire Hydrant
Define a fire hydrant. It provides water access for firefighting. It has
flow rate in L/min, pressure rating in bar, number of outlets, outlet
diameter in mm, connection type (flanged, threaded), and hydrant type
(pillar, underground, wall-mounted). It connects to a water distribution
main and serves a coverage zone. Include last inspection date and color
coding (red, yellow, green, light blue per NFPA flow rating).

### Elevator
An elevator is a vertical transportation system for people or goods. It has
rated capacity in kg, number of stops, travel height in m, speed in m/s,
car dimensions (width, depth, height), door type (center-opening, side-opening),
and drive type (traction, hydraulic, machine-room-less). It serves multiple
storeys and is located in an elevator shaft. Include energy class rating
and annual inspection date.

---

## Cross-Sector / Complex

### Expansion Joint
Define an expansion joint. It accommodates thermal movement between adjacent
structural elements. It has movement capacity in mm, gap width, joint type
(modular, finger, sliding plate, compression seal), installation temperature
in degrees C, and design movement range. It connects two structural elements
(bridge decks, slabs, or wall segments). It belongs to both building and
infrastructure sectors depending on context. Use sector "general".

### Drainage Pipe
A drainage pipe conveys stormwater or wastewater by gravity flow. It has
inner diameter in mm, length, material (PVC, HDPE, concrete, ductile iron),
slope in percent, roughness coefficient (Manning's n), and flow capacity
in L/s. It connects to manholes, catch basins, or outfalls. Include
installation depth and joint type (push-fit, welded, flanged).
