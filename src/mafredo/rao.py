"""
RAO is a data-object for dealing with RAO-type data being
- amplitude [any] and
- phase [radians]

as function of
- heading [degrees]
- omega [rad/s]

This class is created to provide methods for the correct interpolation of this type of data which means
- the amplitude and phase are interpolated separately (interpolation of complex numbers result in incorrect amplitude)
- continuity in heading is considered (eg: interpolation of heading 355 from heading 345 and 0)

An attribute "mode" is added which determine which mode is represented (surge/sway/../yaw) and is needed to determine
how symmetry should be applied (if any). For heave it does not matter whether a wave comes from sb or ps, but for roll it does.

The amplitude and phase can physically be anything. But typically would be one of the following:
- A motion RAO  (result of frequency domain response calculation)
- A force or moment RAO (result of diffraction analysis)
- A response spectrum with relative phase angles (result of a motion RAO combined with a wave-spectrum)

It is suggested to define the wave_direction as the direction of wave propagation relative to the X-axis. So heading 90 is propagation along the y-axis.

functions:

- wave_force_from_capytaine

regrid:

- regrid_frequency
- regrud_headings

symmetry:

- apply_symmetry_xz

others:

- anything from xarray. For example myrao['amplitude'].plot() or myrao['amplitude'].sel(wave_direction=180).values


**Note**
For ease of interpolation the phase is stored internally as "complex_unit" which equals exp(1j*phase). This is a complex
number with angle (phase) and amplitude 1. The relation bewteen these two is:

>>> phase = np.angle(cu)
>>> cu = np.exp(phase *1j)

The complex unit can be obtained as xarray via the __getattr__ method:

>>> cu = my_rao['complex_unit']


"""

import xarray as xr
import numpy as np
from mafredo.helpers import expand_omega_dim_const, expand_direction_to_full_range
from mafredo.helpers import MotionMode, Symmetry, MotionModeToStr


__license__ = "mpl2"

# ----- helpers -----

def _complex_unit_add(data):
    data['complex_unit'] = np.exp(1j * data['phase'])

# def _complex_unit_add_normalize(data):
#     """Normalized the complex units - needs to be done after interpolation"""
#     data['complex_unit'] = data['complex_unit'] / abs(data['complex_unit'])

def _complex_unit_delete(data):
    return data.drop_vars('complex_unit')

def _complex_unit_to_phase(data):
    data['phase'].values = np.angle(data['complex_unit'].values)

# -----------------


class Rao(object):

    def __init__(self):

        # dummy
        self._data = xr.Dataset({
            'amplitude': (['wave_direction', 'omega'], np.zeros((2,2),dtype=float)),
            'phase': (['wave_direction', 'omega'], np.zeros((2,2),dtype=float)),
                    },
            coords={'wave_direction': [0,180],
                    'omega': [0,4],
                    }
        )

        self.mode = None

    @property
    def n_frequencies(self):
        """The number of frequencies in the database"""
        return len(self._data.omega)

    @property
    def n_wave_directions(self):
        """The number of headings or wave-directions """
        return len(self._data.wave_direction)

    @property
    def wave_directions(self):
        return self._data.wave_direction.values


    def to_xarray_nocomplex(self):
        """To xarray with complex numbers separated (netCDF compatibility)"""

        e = self._data.copy(deep=True)
        a = self._data['amplitude'] * self['complex_unit']
        e['real'] = np.real(a)
        e['imag'] = np.imag(a)
        e = e.drop_vars('phase')
        e = e.drop_vars('amplitude')
        return e

    def from_xarray_nocomplex(self, a, mode : MotionMode):
        """From xarray with complex numbers separated (netCDF compatibility)"""

        assert isinstance(mode, MotionMode), 'mode shall be of MotionMode'

        self._data = a.copy(deep=True)
        c = a['real'] + 1j * a['imag']
        self._data['amplitude'] = np.abs(c)
        self._data['phase'] = self._data['amplitude']  # first create dummy copy
        self._data['phase'].values = np.angle(c)       # then set values

        self._data = self._data.drop_vars('real')
        self._data = self._data.drop_vars('imag')
        self.mode = mode

    def set_data(self, directions, omegas, amplitude, phase, mode = None):
        """Sets the data to the provided values.

        Args:
            directions : wave directions
            omegas     : wave frequencies [rad/s]
            amplitude  : wave fores  [iDirection, iOmega]
            phase      : wave phases [iDirection, iOmega] in radians
            mode       : (None) MotionMode - optional, only mandatory when applying symmetry
        """

        """
        The dimensions of the dataset are:
- omega [rad/s]
- wave_direction [deg]
- amplitude [any]
- phase [radians]
        """

        self._data = xr.Dataset({
            'amplitude': (['wave_direction', 'omega'], amplitude),
            'phase': (['wave_direction', 'omega'], phase),
                    },
            coords={'wave_direction': directions,
                    'omega': omegas,
                    }
        )


    def wave_force_from_capytaine(self, filename, mode : MotionMode):
        """
        Reads hydrodynamic data from a netCFD file created with capytaine and copies the
        data for the requested mode into the object.

        Args:
            filename: .nc file to read from
            mode: Name of the mode to read MotionMode

        Returns:
            None

        Examples:
            test = Rao()
            test.wave_force_from_capytaine(r"capytaine.nc", MotionMode.HEAVE)

        """

        from capytaine.io.xarray import merge_complex_values
        dataset = merge_complex_values(xr.open_dataset(filename))

        # wave_direction = dataset['wave_direction'] * (180 / np.pi) # convert rad to deg
        # dataset = dataset.assign_coords(wave_direction = wave_direction)

        if 'excitation_force' not in dataset:
            dataset['excitation_force'] = dataset['Froude_Krylov_force'] + dataset['diffraction_force']

        cmode = MotionModeToStr(mode)

        da = dataset['excitation_force'].sel(influenced_dof = cmode)

        self._data = xr.Dataset()

        self._data['amplitude'] = np.abs(da)

        self._data['phase'] = self._data['amplitude']  # To avoid shape mismatch,
        self._data['phase'].values = np.angle(da)      # first copy with dummy data - then fill

        self.mode = mode

    def regrid_omega(self,new_omega):
        """Regrids the omega axis to new_omega [rad/s] """

        # check if the requested omega values are outside the current frequency grid
        # if so then duplicate the highest or lowest entry to this value

        temp = expand_omega_dim_const(self._data, new_omega)

        _complex_unit_add(temp)
        self._data = temp.interp(omega=new_omega, method='linear')
        _complex_unit_to_phase(self._data)
        self._data = _complex_unit_delete(self._data)

    def regrid_direction(self, new_headings):
        """Regrids the omega axis to new_headings [degrees]. """

        # repeat the zero heading at the zero + 360 / -360 as needed
        expanded =  expand_direction_to_full_range(self._data)
        _complex_unit_add(expanded)
        self._data = expanded.interp(wave_direction=new_headings, method='linear')
        _complex_unit_to_phase(self._data)
        self._data = _complex_unit_delete(self._data)

    def add_direction(self, wave_direction):
        """Adds the given direction to the RAO by interpolation [deg]"""
        headings = self._data['wave_direction'].values
        if wave_direction not in headings:
            new_headings = np.array((*headings, wave_direction), dtype=float)
            new_headings.sort()
            self.regrid_direction(new_headings)

    def add_frequency(self, omega):
        """Adds the given frequency to the RAO by interpolation [rad/s]"""
        frequencies = self._data['omega'].values
        try:
            len(omega)
        except:
            omega = [omega]

        if omega not in frequencies:
            new_omega = np.array((*frequencies, *omega), dtype=float)
            new_omega.sort()
            self.regrid_omega(new_omega)



    def scale(self, factor):
        """Scales the amplitude by the given scale factor (positive numbers only as amplitude can not be negative)"""

        if factor<0:
            raise ValueError('Amplitude can not be negative. If you need an opposite response then apply a phase change of pi')
        self._data['amplitude'] *= factor


    def get_value(self, omega, wave_direction):
        """Returns the value at the requested position.
         If the data-point is not yet available in the database, then the corresponding frequency and wave-direction
         are added to the grid by linear interpolation.

         """

        # Make sure the datapoint is available.
        self.add_direction(wave_direction)  # for linear interpolation the
        self.add_frequency(omega)           # order of interpolations does not matter

        amp =  self._data['amplitude'].sel(wave_direction=wave_direction, omega=omega).values
        cu = self['complex_unit'].sel(wave_direction=wave_direction, omega=omega).values

        return cu * amp


    def add_symmetry_xz(self):
        """Appends equivalent headings considering xz symmetry to the dataset.

        That is:
        The RAO for heading = a is identical to the RAO for heading = -a

        except that for sway, roll and yaw a sign change will be applied (phase shift of pi)

        """



        if self.mode in (MotionMode.SWAY, MotionMode.ROLL, MotionMode.YAW):  # ['SWAY','ROLL','YAW']:
            opposite = True
        elif self.mode in (MotionMode.SURGE, MotionMode.HEAVE, MotionMode.PITCH):# ['SURGE','HEAVE','PITCH']:
            opposite = False
        else:
            raise ValueError('Unknown setting for mode; we need mode to determine how to appy symmetry. Mode setting = {}'.format(mode))

        directions = self._data.coords['wave_direction'].values

        for direction in directions:

            direction_copy = np.mod(-direction, 360)
            if direction_copy in directions:
                continue

            sym = self._data.sel(wave_direction=direction)
            sym.coords['wave_direction'].values = direction_copy

            if opposite:
                sym['phase'] = -sym['phase'] + np.pi

            self._data = xr.concat([self._data, sym], dim='wave_direction')

        self._data = self._data.sortby('wave_direction')

    def __getitem__(self, key):
        if key == "complex_unit":
            _complex_unit_add(self._data)
            return self._data['complex_unit']
        else:
            return self._data[key]

    def __str__(self):
        return str(self._data)

