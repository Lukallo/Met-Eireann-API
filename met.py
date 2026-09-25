import requests
import json

from met_api.geo import haversine

stations = {

    "athenry" : (53.289167, -8.785556),
    "ballyhaise" : (54.051389, -7.309722),
    "belmullet" : (54.227500, -10.006944),
    "oak-park" : (52.861111, -6.915278),
    "casement" : (53.306, -6.439),
    "claremorris" : (53.710833, -8.992500),
    "cork" : (51.847, -8.486),
    "dublin" : (53.428, -6.241),
    "dunsany" : (53.515833, -6.660000),
    "moore-park" : (52.163889, -8.263889),
    "finner" : (54.493889, -8.243056),
    "gurteen" : (53.053056, -8.008611),
    "johnstown-castle" : (52.297778, -6.496667),
    "knock" : (53.906, -8.817),
    "mace-head" : (53.325833, -9.900833),
    "malin-head" : (55.372222, -7.338889),
    "markree-castle" : (54.175000, -8.455556),
    "mt-dillon" : (53.726944, -7.980833),
    "mullingar" : (53.537222, -7.362222),
    "newport-furnace" : (53.922222, -9.572222),
    "phoenix-park" : (53.3636, -6.3497),
    "roches-point" : (51.793056, -8.244444),
    "shannon" : (52.690, -8.918),
    "sherkin-island" : (51.476389, -9.427778),
    "valentia" : (51.939722, -10.244444)
}

auto_stations = {

    8185 : (55.051389, -7.9375, "GLENVEAGH NATIONAL PARK"),
    8285 : (55.150833, -7.348056, "ILLIES WTP"),
    5085 : (54.7675, -7.865, "LOUGH MOURNE WTP"),
    5185 : (54.849722, -7.598889, "RAPHOE (Tops)"),
    4885 : (54.769722, -8.420278, "ARDARA WWTP"),
    4985 : (55.049167, -8.230278, "GWEEDORE WEIR"),
    685 : (54.336667, -6.955833, "EMYVALE WWTP"),
    485 : (53.853611, -6.569444, "ARDEE (Bohernamoe)"),
    585 : (54.051944, -6.351667, "DUNDALK (Annaskeagh)"),
    785 : (54.072778, -6.885278, "KILKIT WTP"),
    5385 : (54.058333, -7.804167, "BALLINAMORE"),
    5885 : (53.833333, -7.423056, "KILCOGY WWTP"),
    7985 : (54.288889, -7.916667, "LOUGHAN HOUSE"),
    8085 : (54.491944, -8.154167, "BALLYSHANNON (Cherrymount)"),
    5285 : (54.279444, -8.598611, "SLIGO AIRPORT"),
    7885 : (54.207778, -8.866944, "LOUGH EASKEY WTP"),
    4485 : (54.309722, -9.568611, "BELDERRIG"),
    4585 : (54.187778, -9.228333, "LISGLENNON WTP"),
    4685 : (53.925, -9.126389, "STRAIDE"),
    4385 : (54.057222, -9.8425, "BALLYCROY"),
    7785 : (54.141389, -9.748333, "BANGOR ERRIS WWTP"),
    885 : (53.698889, -6.715833, "NAVAN (Randalstown)"),
    5585 : (53.9425, -8.326944, "BALLYMORE"),
    5685 : (53.326111, -7.986667, "CLONMACNOISE"),
    7585 : (53.616944, -8.190278, "ROSCOMMON WWTP"),
    1885 : (53.756944, -8.495556, "CASTLEREA WWTP"),
    5485 : (53.468611, -8.493333, "MOUNTBELLEW (Agr Coll)"),
    4085 : (53.286111, -9.055556, "TERRYLAND WTP"),
    4185 : (53.536944, -9.608611, "MAAM VALLEY"),
    4285 : (53.515278, -8.880556, "TUAM WWTP"),
    4785 : (53.681111, -9.371944, "TOURMAKEADY WTP"),
    3985 : (53.551111, -9.942222, "CONNEMARA NATIONAL PARK"),
    7685 : (53.645278, -9.745278, "DOO LOUGH"),
    3885 : (53.359722, -9.339722, "CLOOSH (Forest Station)"),
    1585 : (53.025, -6.075278, "ASHFORD (Cronykeery)"),
    1785 : (52.834167, -6.130556, "ARKLOW (Ballyrichard House)"),
    6185 : (53.245278, -6.115278, "SHANGANAGH WWTP"),
    5985 : (53.369722, -6.270278, "DUBLIN (Glasnevin)"),
    3685 : (53.066389, -8.600278, "GORT (Derrybrien)"),
    2285 : (52.508056, -8.203333, "LIMERICK JUNCTION"),
    3485 : (52.701944, -8.528611, "CLAREVILLE WTP"),
    3385 : (53.030278, -9.0775, "CARRON"),
    3785 : (52.841667, -9.238333, "INAGH (Mount Callan)"),
    6985 : (52.5625, -6.209444, "CAHORE (Kilmichael)"),
    185 : (52.357222, -6.420556, "WEXFORD WILDFOWL RESERVE"),
    6285 : (52.644722, -6.639444, "BUNCLODY WWTP"),
    285 : (52.3175, -6.940833, "JFK PARK"),
    1485 : (53.045278, -7.3075, "PORTLAOISE WWTP"),
    1685 : (53.334722, -7.145556, "EDENDERRY (Ballinla)"),
    7385 : (53.169167, -6.848889, "CURRAGH RACECOURSE"),
    1285 : (52.525, -7.190556, "THOMASTOWN (Mount Juliet)"),
    7485 : (52.992778, -7.704167, "NEALSTOWN"),
    8385 : (52.848056, -7.373889, "DURROW (Castlewood)"),
    1185 : (52.352222, -7.313056, "PILTOWN (Kildalton Agri.College)"),
    1385 : (52.254167, -7.131389, "WATERFORD TYCOR"),
    2085 : (52.458611, -7.829167, "CASHEL (Ballydoyle House)"),
    2185 : (52.688333, -7.832778, "THURLES RACECOURSE"),
    6885 : (52.358056, -7.674722, "CLONMEL WWTP"),
    1985 : (52.553889, -8.785, "ADARE MANOR"),
    3285 : (52.3525, -8.953611, "SPRINGFIELD CASTLE"),
    3585 : (52.856111, -8.756944, "TULLA WTP"),
    2885 : (52.490278, -9.669722, "BALLYBUNION WWTP"),
    6085 : (52.3905, -9.3078, "ABBEYFEALE WWTP"),
    6685 : (52.135, -10.271944, "DINGLE WWTP"),
    1085 : (52.080556, -7.555, "DUNGARVAN WWTP"),
    2385 : (52.33, -8.568889, "MOUNT RUSSELL"),
    3185 : (52.190556, -8.6525, "MALLOW (Hazelwood)"),
    6485 : (51.964722, -7.856667, "YOUGHAL WWTP"),
    6585 : (52.066944, -9.058611, "MILLSTREET (Drishane Castle)"),
    2985 : (52.085833, -9.924722, "DOOKS GOLF CLUB"),
    3085 : (52.022222, -9.501389, "KILLARNEY (Muckross House)"),
    6385 : (51.944444, -9.875, "CLOONE LAKE"),
    6785 : (52.229444, -9.471944, "CASTLEISLAND WWTP"),
    2485 : (52.010833, -8.200556, "BALLINCURRIG (Peafield)"),
    2685 : (51.904167, -8.670278, "INISHCARRA WTP"),
    2585 : (51.626111, -8.851111, "CLONAKILTY (Agri. College)"),
    2785 : (51.721111, -9.100278, "DUNMANWAY WWTP"),
    5785 : (51.736389, -9.544444, "GLENGARRIFF (Ilnacullin)")
}

location = (54.076944, -7.611667)

distances = ( "a", 50000 )
aa = ("a", 50000)

for key, value in auto_stations.items():
    temp = auto_stations[key]
    autodist = haversine(location, (temp[0], temp[1]))
    if autodist < aa[1]:
        aa = (key, autodist)
        print(aa)

print(f"The closest automatic weather station is: {aa[0]}")

for key, value in stations.items():
    dist = haversine(location, value)
    if dist < distances[1]:
        distances = (key, dist)
        print(distances)
    
print(f"The closest weather station is {distances[0]}.")

URL = f"https://prodapi.metweb.ie/observations/{distances[0]}/today"

data = requests.get(URL)

y = json.loads(data.text)





