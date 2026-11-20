"""
Prosopis mapping engine. One module per job; see README.md for the diagram.

    config.py         read config/<plant>.yaml
    tiles.py          the fixed 0.1 degree frame grid
    imagery.py        find scenes; seasons; freshness check
    composite.py      load scenes, mask clouds, one median picture per season
    features.py       the ONE place features are defined (training and scanning)
    labels.py         training points from the Dryad map, and later from crews
    train.py          fit the Random Forest; spatial cross-validation; model card
    applicability.py  where the model is on familiar ground (Meyer & Pebesma 2021)
    summarize.py      pixels -> cells -> density classes and areas with a range
    gate.py           PLAN CREWS / SCOUT FIRST / CAN'T JUDGE HERE
    field_plan.py     plot and check-point locations for crews
    change.py         compare two seasons of the same frame
    height.py         tree height + cover per cell from the open Meta/WRI height map
    field_check.py    how well the maps match the scouts' plots
    index.py          dashboard/data/index.json
    scan.py           run all of the above for one frame and season
"""
