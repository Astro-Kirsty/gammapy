# Licensed under a 3-clause BSD style license - see LICENSE.rst
import pytest
import numpy as np
from numpy.testing import assert_allclose
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.units import Unit
from gammapy.data import DataStore
from gammapy.irf import PSF3D, EffectiveAreaTable2D, PSFMap
from gammapy.makers.utils import make_map_exposure_true_energy, make_psf_map
from gammapy.maps import MapAxis, MapCoord, WcsGeom, RegionGeom, Map
from gammapy.utils.testing import requires_data


@pytest.fixture(scope="session")
def data_store():
    return DataStore.from_dir("$GAMMAPY_DATA/hess-dl3-dr1/")


def fake_psf3d(sigma=0.15 * u.deg, shape="gauss"):
    offset_axis = MapAxis.from_nodes([0, 1, 2, 3] * u.deg, name="offset")

    energy_axis_true = MapAxis.from_energy_bounds(
        "0.1 TeV", "10 TeV", nbin=4, name="energy_true"
    )

    rad = np.linspace(0, 1.0, 101) * u.deg
    rad_axis = MapAxis.from_edges(rad, name="rad")

    O, R, E = np.meshgrid(offset_axis.center, rad_axis.edges, energy_axis_true.center)

    Rmid = 0.5 * (R[:-1] + R[1:])
    if shape == "gauss":
        val = np.exp(-0.5 * Rmid ** 2 / sigma ** 2)
    else:
        val = Rmid < sigma

    drad = 2 * np.pi * (np.cos(R[:-1]) - np.cos(R[1:])) * u.Unit("sr")
    psf_value = val / ((val * drad).sum(0)[0])

    return PSF3D(
        axes=[energy_axis_true, offset_axis, rad_axis],
        data=psf_value.T.value,
        unit=psf_value.unit
    )


def fake_aeff2d(area=1e6 * u.m ** 2):
    energy_axis_true = MapAxis.from_energy_bounds(
        "0.1 TeV", "10 TeV", nbin=4, name="energy_true"
    )

    offset_axis = MapAxis.from_edges(
        [0.0, 1.0, 2.0, 3.0] * u.deg, name="offset"
    )

    return EffectiveAreaTable2D(
        axes=[energy_axis_true, offset_axis], data=area.value, unit=area.unit
     )


def test_make_psf_map():
    psf = fake_psf3d(0.3 * u.deg)

    pointing = SkyCoord(0, 0, unit="deg")
    energy_axis = MapAxis(
        nodes=[0.2, 0.7, 1.5, 2.0, 10.0], unit="TeV", name="energy_true"
    )
    rad_axis = MapAxis(nodes=np.linspace(0.0, 1.0, 51), unit="deg", name="rad")

    geom = WcsGeom.create(
        skydir=pointing, binsz=0.2, width=5, axes=[rad_axis, energy_axis]
    )

    psfmap = make_psf_map(psf, pointing, geom)

    assert psfmap.psf_map.geom.axes[0] == rad_axis
    assert psfmap.psf_map.geom.axes[1] == energy_axis
    assert psfmap.psf_map.unit == Unit("sr-1")
    assert psfmap.psf_map.data.shape == (4, 50, 25, 25)


def make_test_psfmap(size, shape="gauss"):
    psf = fake_psf3d(size, shape)
    aeff2d = fake_aeff2d()

    pointing = SkyCoord(0, 0, unit="deg")
    energy_axis = MapAxis(
        nodes=[0.2, 0.7, 1.5, 2.0, 10.0], unit="TeV", name="energy_true"
    )
    rad_axis = MapAxis.from_edges(
        edges=np.linspace(0.0, 1, 101), unit="deg", name="rad"
    )

    geom = WcsGeom.create(
        skydir=pointing, binsz=0.2, width=5, axes=[rad_axis, energy_axis]
    )

    exposure_geom = geom.squash(axis_name="rad")

    exposure_map = make_map_exposure_true_energy(pointing, "1 h", aeff2d, exposure_geom)

    return make_psf_map(psf, pointing, geom, exposure_map)


def test_psf_map_containment():
    psf_map = make_test_psfmap(0.15 * u.deg)
    psf = fake_psf3d(0.15 * u.deg)

    position = SkyCoord(0, 0, unit="deg")

    # Check that containment radius is consistent between psf_table and psf3d
    assert_allclose(
        psf_map.containment_radius(
            energy_true=1 * u.TeV, position=position, fraction=0.9
        ),
        psf.containment_radius(
            energy_true=1 * u.TeV, offset=0 * u.deg, fraction=0.9
        ),
        rtol=1e-2,
    )
    assert_allclose(
        psf_map.containment_radius(
            energy_true=1 * u.TeV, position=position, fraction=0.5
        ),
        psf.containment_radius(
            energy_true=1 * u.TeV, offset=0 * u.deg, fraction=0.5
        ),
        rtol=1e-2,
    )


def test_psfmap_to_psf_kernel():
    psfmap = make_test_psfmap(0.15 * u.deg)

    energy_axis = psfmap.psf_map.geom.axes[1]
    # create PSFKernel
    kern_geom = WcsGeom.create(binsz=0.02, width=5.0, axes=[energy_axis])
    psfkernel = psfmap.get_psf_kernel(
        position=SkyCoord(1, 1, unit="deg"), geom=kern_geom, max_radius=1 * u.deg
    )
    assert_allclose(psfkernel.psf_kernel_map.data.sum(axis=(1, 2)), 1.0, atol=1e-7)


def test_psfmap_to_from_hdulist():
    psfmap = make_test_psfmap(0.15 * u.deg)
    hdulist = psfmap.to_hdulist()
    assert "PSF" in hdulist
    assert "PSF_BANDS" in hdulist
    assert "PSF_EXPOSURE" in hdulist
    assert "PSF_EXPOSURE_BANDS" in hdulist

    new_psfmap = PSFMap.from_hdulist(hdulist)
    assert_allclose(psfmap.psf_map.data, new_psfmap.psf_map.data)
    assert new_psfmap.psf_map.geom == psfmap.psf_map.geom
    assert new_psfmap.exposure_map.geom == psfmap.exposure_map.geom


def test_psfmap_read_write(tmp_path):
    psfmap = make_test_psfmap(0.15 * u.deg)

    psfmap.write(tmp_path / "tmp.fits")
    new_psfmap = PSFMap.read(tmp_path / "tmp.fits")

    assert_allclose(psfmap.psf_map.quantity, new_psfmap.psf_map.quantity)


def test_containment_radius_map():
    psf = fake_psf3d(0.15 * u.deg)
    pointing = SkyCoord(0, 0, unit="deg")
    energy_axis = MapAxis(nodes=[0.2, 1, 2], unit="TeV", name="energy_true")
    psf_theta_axis = MapAxis(nodes=np.linspace(0.0, 0.6, 30), unit="deg", name="rad")
    geom = WcsGeom.create(
        skydir=pointing, binsz=0.5, width=(4, 3), axes=[psf_theta_axis, energy_axis]
    )

    psfmap = make_psf_map(psf=psf, pointing=pointing, geom=geom)
    m = psfmap.containment_radius_map(energy_true=1 * u.TeV)
    coord = SkyCoord(0.3, 0, unit="deg")
    val = m.interp_by_coord(coord)
    assert_allclose(val, 0.226477, rtol=1e-2)


def test_psfmap_stacking():
    psfmap1 = make_test_psfmap(0.1 * u.deg, shape="flat")
    psfmap2 = make_test_psfmap(0.1 * u.deg, shape="flat")
    psfmap2.exposure_map.quantity *= 2

    psfmap_stack = psfmap1.copy()
    psfmap_stack.stack(psfmap2)
    assert_allclose(psfmap_stack.psf_map.data, psfmap1.psf_map.data)
    assert_allclose(psfmap_stack.exposure_map.data, psfmap1.exposure_map.data * 3)

    psfmap3 = make_test_psfmap(0.3 * u.deg, shape="flat")

    psfmap_stack = psfmap1.copy()
    psfmap_stack.stack(psfmap3)

    assert_allclose(psfmap_stack.psf_map.data[0, 40, 20, 20], 0.0)
    assert_allclose(psfmap_stack.psf_map.data[0, 20, 20, 20], 5805.28955078125)
    assert_allclose(psfmap_stack.psf_map.data[0, 0, 20, 20], 58052.78955078125)


# TODO: add a test comparing make_mean_psf and PSFMap.stack for a set of observations in an Observations


def test_sample_coord():
    psf_map = make_test_psfmap(0.1 * u.deg, shape="gauss")

    coords_in = MapCoord(
        {"lon": [0, 0] * u.deg, "lat": [0, 0.5] * u.deg, "energy_true": [1, 3] * u.TeV},
        frame="icrs",
    )

    coords = psf_map.sample_coord(map_coord=coords_in)
    assert coords.frame == "icrs"
    assert len(coords.lon) == 2
    assert_allclose(coords.lon, [0.074855, 0.042655], rtol=1e-3)
    assert_allclose(coords.lat, [-0.101561, 0.347365], rtol=1e-3)


def test_sample_coord_gauss():
    psf_map = make_test_psfmap(0.1 * u.deg, shape="gauss")

    lon, lat = np.zeros(10000) * u.deg, np.zeros(10000) * u.deg
    energy = np.ones(10000) * u.TeV
    coords_in = MapCoord.create(
        {"lon": lon, "lat": lat, "energy_true": energy}, frame="icrs"
    )
    coords = psf_map.sample_coord(coords_in)

    assert_allclose(np.mean(coords.skycoord.data.lon.wrap_at("180d").deg), 0, atol=2e-3)
    assert_allclose(np.mean(coords.lat), 0, atol=2e-3)


def make_psf_map_obs(geom, obs):
    exposure_map = make_map_exposure_true_energy(
        geom=geom.squash(axis_name="rad"),
        pointing=obs.pointing_radec,
        aeff=obs.aeff,
        livetime=obs.observation_live_time_duration,
    )

    psf_map = make_psf_map(
        geom=geom, psf=obs.psf, pointing=obs.pointing_radec, exposure_map=exposure_map
    )
    return psf_map


@requires_data()
@pytest.mark.parametrize(
    "pars",
    [
        {
            "energy": None,
            "rad": None,
            "energy_shape": 32,
            "psf_energy": 0.8659643,
            "rad_shape": 144,
            "psf_rad": 0.0015362848,
            "psf_exposure": 3.14711e12 * u.Unit("cm2 s"),
            "psf_value_shape": (32, 144),
            "psf_value": 4369.96391 * u.Unit("sr-1"),
        },
        {
            "energy": MapAxis.from_energy_bounds(1, 10, 100, "TeV", name="energy_true"),
            "rad": None,
            "energy_shape": 100,
            "psf_energy": 1.428893959,
            "rad_shape": 144,
            "psf_rad": 0.0015362848,
            "psf_exposure": 4.723409e12 * u.Unit("cm2 s"),
            "psf_value_shape": (100, 144),
            "psf_value": 3714.303683 * u.Unit("sr-1"),
        },
        {
            "energy": None,
            "rad": MapAxis.from_nodes(np.arange(0, 2, 0.002), unit="deg", name="rad"),
            "energy_shape": 32,
            "psf_energy": 0.8659643,
            "rad_shape": 1000,
            "psf_rad": 0.000524,
            "psf_exposure": 3.14711e12 * u.Unit("cm2 s"),
            "psf_value_shape": (32, 1000),
            "psf_value": 25888.5047 * u.Unit("sr-1"),
        },
        {
            "energy": MapAxis.from_energy_bounds(1, 10, 100, "TeV", name="energy_true"),
            "rad": MapAxis.from_nodes(np.arange(0, 2, 0.002), unit="deg", name="rad"),
            "energy_shape": 100,
            "psf_energy": 1.428893959,
            "rad_shape": 1000,
            "psf_rad": 0.000524,
            "psf_exposure": 4.723409e12 * u.Unit("cm2 s"),
            "psf_value_shape": (100, 1000),
            "psf_value": 22453.412121 * u.Unit("sr-1"),
        },
    ],
)
def test_make_psf(pars, data_store):
    obs = data_store.obs(23523)
    psf = obs.psf

    if pars["energy"] is None:
        energy_axis = psf.axes["energy_true"]
    else:
        energy_axis = pars["energy"]

    if pars["rad"] is None:
        rad_axis = psf.axes["rad"]
    else:
        rad_axis = pars["rad"]

    position = SkyCoord(83.63, 22.01, unit="deg")

    geom = WcsGeom.create(
        skydir=position, npix=(3, 3), axes=[rad_axis, energy_axis], binsz=0.2
    )

    psf_map = make_psf_map_obs(geom, obs)
    psf = psf_map.to_region_nd_map(position)

    axis = psf.psf_map.geom.axes["energy_true"]
    assert axis.unit == "TeV"
    assert axis.nbin == pars["energy_shape"]
    assert_allclose(axis.center.value[15], pars["psf_energy"], rtol=1e-3)

    rad_axis = psf.psf_map.geom.axes["rad"]
    assert rad_axis.unit == "deg"
    assert rad_axis.nbin == pars["rad_shape"]
    assert_allclose(rad_axis.center.to_value("rad")[15], pars["psf_rad"], rtol=1e-3)

    exposure = psf.exposure_map.quantity.squeeze()
    assert exposure.unit == "m2 s"
    assert exposure.shape == (pars["energy_shape"],)
    assert_allclose(exposure[15], pars["psf_exposure"], rtol=1e-3)

    data = psf.psf_map.quantity.squeeze()
    assert data.unit == "sr-1"
    assert data.shape == pars["psf_value_shape"]
    assert_allclose(data[15, 50], pars["psf_value"], rtol=1e-3)


@requires_data()
def test_make_mean_psf(data_store):
    observations = data_store.get_observations([23523, 23526])
    position = SkyCoord(83.63, 22.01, unit="deg")

    psf = observations[0].psf

    geom = WcsGeom.create(
        skydir=position,
        npix=(3, 3),
        axes=psf.axes[["rad", "energy_true"]],
        binsz=0.2,
    )

    psf_map_1 = make_psf_map_obs(geom, observations[0])
    psf_map_2 = make_psf_map_obs(geom, observations[1])

    stacked_psf = psf_map_1.copy()
    stacked_psf.stack(psf_map_2)

    psf = stacked_psf.to_region_nd_map(position).psf_map

    assert not np.isnan(psf.quantity.squeeze()).any()
    assert_allclose(psf.quantity.squeeze()[22, 22], 12206.1665 / u.sr, rtol=1e-3)


@requires_data()
@pytest.mark.parametrize("position", ["0d 0d", "180d 0d", "0d 90d", "180d -90d"])
def test_psf_map_read(position):
    position = SkyCoord(position)
    filename = "$GAMMAPY_DATA/fermi_3fhl/fermi_3fhl_psf_gc.fits.gz"
    psf = PSFMap.read(filename, format="gtpsf")

    value = psf.containment(
        position=position, energy_true=100 * u.GeV, rad=0.1 * u.deg
    )

    assert_allclose(value, 0.682032, rtol=1e-5)
    assert psf.psf_map.unit == "sr-1"


def test_psf_map_write_gtpsf(tmpdir):
    energy_axis_true = MapAxis.from_energy_bounds(
        "1 TeV", "10 TeV", nbin=3, name="energy_true"
    )
    geom = RegionGeom.create("icrs;circle(0, 0, 0.1)")
    psf = PSFMap.from_gauss(
        energy_axis_true=energy_axis_true, sigma=[0.1, 0.2, 0.3] * u.deg, geom=geom
    )
    psf.exposure_map = Map.from_geom(geom.to_cube([energy_axis_true]), unit="cm2 s")

    filename = tmpdir / "test_psf.fits"
    psf.write(filename, format="gtpsf")

    psf = PSFMap.read(filename, format="gtpsf")

    value = psf.containment_radius(
        energy_true=energy_axis_true.center, fraction=0.394
    )

    assert_allclose(value, [0.1, 0.2, 0.3] * u.deg, rtol=1e-5)
    assert psf.psf_map.unit == "sr-1"


def test_to_image():
    psfmap = make_test_psfmap(0.15 * u.deg)

    psf2D = psfmap.to_image()
    assert_allclose(psf2D.psf_map.geom.data_shape, (1, 100, 25, 25))
    assert_allclose(psf2D.exposure_map.geom.data_shape, (1, 1, 25, 25))
    assert_allclose(psf2D.psf_map.data[0][0][12][12], 23255.41204827, rtol=1e-2)


def test_psf_map_from_gauss():
    energy_axis = MapAxis.from_nodes(
        [1, 3, 10], name="energy_true", interp="log", unit="TeV"
    )
    rad = np.linspace(0, 1.5, 100) * u.deg
    rad_axis = MapAxis.from_nodes(rad, name="rad", unit="deg")

    # define sigmas starting at 0.1 in steps of 0.1 deg
    sigma = [0.1, 0.2, 0.4] * u.deg

    # with energy-dependent sigma
    psfmap = PSFMap.from_gauss(energy_axis, rad_axis, sigma)

    assert psfmap.psf_map.geom.axes[0] == rad_axis
    assert psfmap.psf_map.geom.axes[1] == energy_axis
    assert psfmap.psf_map.unit == "sr-1"
    assert psfmap.psf_map.data.shape == (3, 100, 1, 2)

    radius = psfmap.containment_radius(
        fraction=0.394, energy_true=[1, 3, 10] * u.TeV
    )
    assert_allclose(radius, sigma, rtol=0.01)

    # test that it won't work with different number of sigmas and energies
    with pytest.raises(ValueError):
        PSFMap.from_gauss(energy_axis, rad_axis, sigma=[1, 2] * u.deg)


def test_psf_map_from_gauss_const_sigma():
    energy_axis = MapAxis.from_nodes(
        [1, 3, 10], name="energy_true", interp="log", unit="TeV"
    )
    rad = np.linspace(0, 1.5, 100) * u.deg
    rad_axis = MapAxis.from_nodes(rad, name="rad", unit="deg")

    # with constant sigma
    psfmap = PSFMap.from_gauss(energy_axis, rad_axis, sigma=0.1 * u.deg)
    assert psfmap.psf_map.geom.axes[0] == rad_axis
    assert psfmap.psf_map.geom.axes[1] == energy_axis
    assert psfmap.psf_map.unit == Unit("sr-1")
    assert psfmap.psf_map.data.shape == (3, 100, 1, 2)

    radius = psfmap.containment_radius(
        energy_true=[1, 3, 10] * u.TeV, fraction=0.394
    )
    assert_allclose(radius, 0.1 * u.deg, rtol=0.01)
