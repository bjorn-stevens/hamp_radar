import xarray as xr
import seaborn as sns
# %%

flight_index = "HALO-20240811a"
radar_data = f"products/HALO/radar/moments/{flight_index}.zarr"
ipfs_path = "ipns://latest.orcestra-campaign.org/"
ds = xr.open_zarr(ipfs_path + radar_data)
# %%

dx = ds.sel(time=slice("2024-08-11T15:36", "2024-08-11T16:06"))
dx.dBZe.plot.pcolormesh(
    x="time",
    figsize=(5, 2),
    vmin=-30,
    vmax=20,
    cbar_kwargs={"aspect": 16.0, "shrink": 0.5},
)
sns.despine(offset=10)
# %%
