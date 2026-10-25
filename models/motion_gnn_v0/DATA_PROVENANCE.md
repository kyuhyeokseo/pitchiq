# Data provenance: IDSSE open tracking data

- **Citation:** Bassek, M., Rein, R., Weber, H. & Memmert, D. (2025). An integrated dataset of spatiotemporal and event data in elite soccer. Scientific Data 12, 195. https://doi.org/10.1038/s41597-025-04505-y. Data: https://doi.org/10.6084/m9.figshare.28196177 (v1).
- **Licence:** CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), published with the DFL's authorisation. The paper's data record says the data set "is released under a CC-BY 4.0 license with the
  authorization from the DFL".
- **Attribution and changes:**
  - referee tracks and all names dropped;
  - 25 Hz resampled to 5 Hz;
  - coordinates moved from the centre origin to PitchIQ's bottom-left 105 × 68 m frame;
  - rotated so that the team in possession attacks +x.
- **Not redistributed:** the raw and processed data stay outside this repository and are never part of
  `data/public/` or the serving image. Only the trained weights and aggregate statistics are published.
- **Not used:** event data (counted in the audit only), player names, shirt numbers, match ids as features, and
  video (not part of the data set).
- **Contents:** 7 matches of the 2022/23 season (2 Bundesliga, 5 2. Bundesliga). Each match has a match
  information, an events and a positions XML file. The positions are TRACAB Gen5 at 25 Hz in metres, with the
  origin at the pitch centre. The ball carries height, BallPossession (1 home / 2 away) and BallStatus (1 in play).
  Players keep persistent DFL person ids.

## Audit (measured from the files)
| match | competition | half | frames (25 Hz, ball) | minutes | in play | possession known in play | persons tracked home/away | mid-track gap frames (5 Hz) | goalkeepers tracked | home attacks |
|---|---|---|---|---|---|---|---|---|---|---|
| DFL-MAT-J03WMX | Bundesliga | firstHalf | 70708 | 47.1 | 0.668 | 1.0 | 11/11 | 0 | 2 | -x |
| DFL-MAT-J03WMX | Bundesliga | secondHalf | 75259 | 50.2 | 0.507 | 1.0 | 16/16 | 0 | 2 | +x |
| DFL-MAT-J03WN1 | Bundesliga | firstHalf | 69288 | 46.2 | 0.559 | 1.0 | 11/11 | 0 | 2 | -x |
| DFL-MAT-J03WN1 | Bundesliga | secondHalf | 72273 | 48.2 | 0.41 | 1.0 | 16/12 | 0 | 2 | +x |
| DFL-MAT-J03WOH | 2. Bundesliga | firstHalf | 69424 | 46.3 | 0.545 | 1.0 | 11/11 | 0 | 2 | -x |
| DFL-MAT-J03WOH | 2. Bundesliga | secondHalf | 67790 | 45.2 | 0.503 | 1.0 | 16/16 | 0 | 2 | +x |
| DFL-MAT-J03WOY | 2. Bundesliga | firstHalf | 69111 | 46.1 | 0.546 | 1.0 | 11/12 | 0 | 2 | -x |
| DFL-MAT-J03WOY | 2. Bundesliga | secondHalf | 73425 | 49.0 | 0.525 | 1.0 | 16/14 | 0 | 2 | +x |
| DFL-MAT-J03WPY | 2. Bundesliga | firstHalf | 69131 | 46.1 | 0.668 | 1.0 | 11/11 | 0 | 2 | -x |
| DFL-MAT-J03WPY | 2. Bundesliga | secondHalf | 77080 | 51.4 | 0.458 | 1.0 | 15/16 | 0 | 2 | +x |
| DFL-MAT-J03WQQ | 2. Bundesliga | firstHalf | 69204 | 46.1 | 0.681 | 1.0 | 11/11 | 0 | 2 | -x |
| DFL-MAT-J03WQQ | 2. Bundesliga | secondHalf | 73141 | 48.8 | 0.51 | 1.0 | 15/16 | 0 | 2 | +x |
| DFL-MAT-J03WR9 | 2. Bundesliga | firstHalf | 68354 | 45.6 | 0.648 | 1.0 | 11/11 | 0 | 2 | +x |
| DFL-MAT-J03WR9 | 2. Bundesliga | secondHalf | 78456 | 52.3 | 0.523 | 1.0 | 15/14 | 0 | 2 | -x |

## Downloaded files (figshare MD5 check)
```
DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-J03WN1.xml  11722  md5 37b0f85588c0acfb995fe633c7ac2e0d  OK
DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-J03WMX.xml  11930  md5 bc5d5b21dbcf36aa30f4cd0428309f6b  OK
DFL_02_01_matchinformation_DFL-COM-000002_DFL-MAT-J03WOH.xml  11745  md5 b9710781d400dd812f62b0a6e3d7bce8  OK
DFL_02_01_matchinformation_DFL-COM-000002_DFL-MAT-J03WR9.xml  11823  md5 5e2d580d3d8ed97e32959de90d04992b  OK
DFL_02_01_matchinformation_DFL-COM-000002_DFL-MAT-J03WPY.xml  11922  md5 65a4568e4f374af94d3c1015cbc7c4f5  OK
DFL_02_01_matchinformation_DFL-COM-000002_DFL-MAT-J03WQQ.xml  11932  md5 5979209af316a914ce57347d0633df6c  OK
DFL_02_01_matchinformation_DFL-COM-000002_DFL-MAT-J03WOY.xml  11952  md5 7b021e577fca917a63111d55cc29ae5b  OK
DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-J03WMX.xml  776578  md5 2d88e180a1f63c7e4157feb980fc7255  OK
DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-J03WN1.xml  612114  md5 83e8ed7ea8e8ebbbdc61f61ac3534008  OK
DFL_03_02_events_raw_DFL-COM-000002_DFL-MAT-J03WOH.xml  635747  md5 ab66e51512f39d06f49f27dd3b4c2f40  OK
DFL_03_02_events_raw_DFL-COM-000002_DFL-MAT-J03WOY.xml  683018  md5 64b1be01d6ff6848168efebd56f344b9  OK
DFL_03_02_events_raw_DFL-COM-000002_DFL-MAT-J03WPY.xml  698898  md5 f68482441d38723d69f244f896482803  OK
DFL_03_02_events_raw_DFL-COM-000002_DFL-MAT-J03WQQ.xml  732186  md5 b7e9d82af4034539d802319be6c57776  OK
DFL_03_02_events_raw_DFL-COM-000002_DFL-MAT-J03WR9.xml  681050  md5 c13bc5efa5eca4a022cc678210b87d5f  OK
DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-J03WMX.xml  418524833  md5 e4273a2adf5dbd9d4ec42def2f01905c  OK
DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-J03WN1.xml  391722803  md5 bacb2f609c1d1b0bfc906f6b9d4109f2  OK
DFL_04_03_positions_raw_observed_DFL-COM-000002_DFL-MAT-J03WOH.xml  348945715  md5 3efff25a86a61768e7d253b4bba137a3  OK
DFL_04_03_positions_raw_observed_DFL-COM-000002_DFL-MAT-J03WOY.xml  362567859  md5 4e1063e0a0ae499920cf68ac1aa3ab4e  OK
DFL_04_03_positions_raw_observed_DFL-COM-000002_DFL-MAT-J03WPY.xml  371688499  md5 e56ff1336a334f8da51110fbe7cab764  OK
DFL_04_03_positions_raw_observed_DFL-COM-000002_DFL-MAT-J03WQQ.xml  356219423  md5 9105bad987191d3c3c362c1fce9a4fcd  OK
DFL_04_03_positions_raw_observed_DFL-COM-000002_DFL-MAT-J03WR9.xml  372934140  md5 e060bb7d7a5a3bfbd15bbf9bc631024d  OK
```
