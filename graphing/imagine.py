# TODO: docstrings (when stable)
# TODO: unit tests

from copy import deepcopy
import functools
import logging
import warnings
import time
from collections import Callable

import numpy as np
import matplotlib.pylab as plt
from IPython import embed
from matplotlib import ticker
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec
from matplotlib.widgets import Slider
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Line3DCollection
from mpl_toolkits.axes_grid1 import AxesGrid, make_axes_locatable

from recipes.logging import LoggingMixin
from recipes.introspection.utils import get_module_name
# from .zscale import zrange
from .sliders import TripleSliders
from .draggable.machinery import Observers

# from astropy.visualization import mpl_normalize  # import ImageNormalize as _
from astropy.visualization.mpl_normalize import ImageNormalize
from astropy.visualization.interval import BaseInterval
from astropy.visualization.stretch import BaseStretch

from .utils import get_percentile_limits

import itertools as itt

# module level logger
logger = logging.getLogger(get_module_name(__file__))


# TODO: maybe display things like contrast ratio ??

def _sanitize_data(data):
    """
    Removes nans and masked elements
    Returns flattened array
    """
    if np.ma.is_masked(data):
        data = data[~data.mask]
    try:
        return np.asarray(data[~np.isnan(data)])
    except Exception as err:
        from IPython import embed
        from recipes.io.utils import TracebackWrapper
        import textwrap, traceback
        embed(header=textwrap.dedent(
                """\
                Caught the following:
                %s
                %s
                Exception will be re-raised upon exiting this embedded interpreter.
                """) % (err, traceback.format_exc()))
        raise


def move_axes(ax, x, y):
    """Move the axis in the figure by x, y"""
    l, b, w, h = ax.get_position(True).bounds
    ax.set_position((l + x, b + y, w, h))


def get_norm(image, interval, stretch):
    """

    Parameters
    ----------
    image
    interval
    stretch

    Returns
    -------

    """
    # choose colour interval algorithm based on data type
    if image.dtype.kind == 'i':  # integer array
        if image.ptp() < 1000:
            interval = 'minmax'

    # determine colour transform from `interval` and `stretch`
    if isinstance(interval, str):
        interval = interval,
    interval = Interval.from_name(*interval)
    #
    if isinstance(stretch, str):
        stretch = stretch,
    stretch = Stretch.from_name(*stretch)

    # Create an ImageNormalize object
    return ImageNormalize(image, interval, stretch=stretch)


def get_screen_size_inches():
    """
    Use QT to get the size of the primary screen in inches

    Returns
    -------
    size_inches: list

    """
    import sys
    from PyQt5.QtWidgets import QApplication, QDesktopWidget

    # Note the check on QApplication already running and not executing the exit
    #  statement at the end.
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    else:
        logger.debug(f'QApplication instance already exists: {app}')

    # TODO: find out on which screen the focus is

    w = QDesktopWidget()
    s = w.screen()
    size_inches = [s.width() / s.physicalDpiX(), s.height() / s.physicalDpiY()]
    # app.exec_()
    w.close()
    return size_inches

    # screens = app.screens()
    # size_inches = np.empty((len(screens), 2))
    # for i, s in enumerate(screens):
    #     g = s.geometry()
    #     size_inches[i] = np.divide(
    #             [g.height(), g.width()], s.physicalDotsPerInch()
    #     )
    # app.exec_()
    # return size_inches


def guess_figsize(image, fill_factor=0.75, max_pixel_size=0.2):
    """
    Make an educated guess of the size of the figure needed to display the
    image data.

    Parameters
    ----------
    image: np.ndarray
        Sample image
    fill_factor: float
        Maximal fraction of screen size allowed in any direction
    min_size: 2-tuple
        Minimum allowed size (width, height) in inches
    max_pixel_size: float
        Maximum allowed pixel size

    Returns
    -------
    size: tuple
        Size (width, height) of the figure in inches


    """

    # Sizes reported by mpl figures seem about half the actual size on screen
    shape = np.array(np.shape(image)[::-1])
    return _guess_figsize(shape, fill_factor, max_pixel_size)


def _guess_figsize(image_shape, fill_factor=0.75, max_pixel_size=0.2):
    # screen dimensions
    screen_size = np.array(get_screen_size_inches())
    # change order of image dimensions since opposite order of screen

    # get upper limit for fig size based on screen and data and fill factor
    max_size = screen_size * fill_factor  # maximal size
    scale = np.min(image_shape / max_size)
    size = image_shape / scale

    # size = ((shape / max(shape)) * screen_size[np.argmax(shape)] * fill_factor)
    # max_size = shape * max_pixel_size
    # if np.any(size > max_size):
    #     size = max_size

    # if np.any(size1 < min_size)
    # other, one dimension might be less than min_size.
    # if the image is elongated, ie one dimension much smaller than the
    # figSize = np.where(size1 < min_size, min_size, figSize)
    logger.debug('Guessed figure size: (%.1f, %.1f)', *size)
    return size


def auto_grid(n):
    x = int(np.floor(np.sqrt(n)))
    y = int(np.ceil(n / x))
    return x, y


def set_clim_connected(x, y, artist, sliders):
    artist.set_clim(*sliders.positions)
    return artist


def plot_image_grid(images, layout=(), titles=(), figsize=None, plims=None,
                    clim_all=False):
    """

    Parameters
    ----------
    images
    layout
    titles
    clim_all:
        Compute colour limits from the full set of pixel values for all
        images.  Choose this if your images are all normalised to roughly the
        same scale. If False clims will be computed individually and the
        colourbar sliders will be disabled.

    Returns
    -------

    """

    # TODO: plot individual histograms as well as full hist
    # todo: guess fig size

    n = len(images)
    assert n, 'No images to plot!'
    # assert clim_mode in ('all', 'row')

    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    # get grid layout
    if not layout:
        layout = auto_grid(n)
    n_rows, n_cols = layout

    # create figure
    fig = plt.figure(figsize=figsize)

    # Use gridspec rather than ImageGrid since the latter tends to resize
    # the axes
    if clim_all:
        cbar_size, hist_size = 3, 5
    else:
        cbar_size = hist_size = 0

    gs = GridSpec(n_rows, n_cols * (100 + cbar_size + hist_size),
                  hspace=0.005,
                  wspace=0.005,
                  left=0.03,  # fixme: need more for ticks
                  right=0.97,
                  bottom=0.03,
                  top=0.98
                  )  # todo: maybe better with tight layout.

    # create colourbar and pixel histogram axes
    kws = dict(origin='lower left',
               cbar=False, sliders=False, hist=False,
               clim=not clim_all,
               plims=plims)

    art = []
    w = len(str(int(n)))
    axes = np.empty((n_rows, n_cols), 'O')
    indices = enumerate(np.ndindex(n_rows, n_cols))
    for (i, (j, k)), title in itt.zip_longest(indices, titles, fillvalue=''):
        if i == n:
            break

        # last
        if (i == n - 1) and clim_all:
            # do colourbar + pixel histogram if clim all
            kws.update(cbar=True, sliders=True, hist=True,
                       cax=fig.add_subplot(
                               gs[:, -(cbar_size + hist_size) * n_cols:]),
                       hax=fig.add_subplot(gs[:, -hist_size * n_cols:]))

        # create axes!
        axes[j, k] = ax = fig.add_subplot(
                gs[j:j + 1, (100 * k):(100 * (k + 1))])

        # plot image. use imshow for all but last

        imd = ImageDisplay(images[i], ax=ax, **kws)
        artist = imd.imagePlot
        # else:
        #     artist = ax.imshow(images[i], **kws)
        #
        art.append(artist)

        # do ticks
        top = (j == 0)
        bot = (j == n_rows - 1)
        # right = (j == n_cols - 1)
        if k != 0:  # not leftmost
            ax.set_yticklabels([])
        if not (bot or top):
            ax.set_xticklabels([])
        # if right:
        #     ax.yaxis.tick_right()
        if top:
            ax.xaxis.tick_top()

        # add title text
        title = title.replace("\n", "\n     ")
        ax.text(0.025, 0.95, f'{i: <{w}}: {title}',
                color='w', va='top', fontweight='bold', transform=ax.transAxes)

    # Do colorbar
    # noinspection PyUnboundLocalVariable
    # fig.colorbar(imd.imagePlot, cax)

    if clim_all:
        # connect all image clims to the sliders.
        for artist in art:
            # noinspection PyUnboundLocalVariable
            imd.sliders.lower.on_move.add(set_clim_connected, artist,
                                          imd.sliders)
            imd.sliders.upper.on_move.add(set_clim_connected, artist,
                                          imd.sliders)

        # The same as above can be accomplished in pure matplolib as follows:
        # https://matplotlib.org/3.1.1/gallery/images_contours_and_fields/multi_image.html
        # Make images respond to changes in the norm of other images (e.g. via
        # the "edit axis, curves and images parameters" GUI on Qt), but be
        # careful not to recurse infinitely!
        # def update(changed_image):
        #     for im in art:
        #         if (changed_image.get_cmap() != im.get_cmap()
        #                 or changed_image.get_clim() != im.get_clim()):
        #             im.set_cmap(changed_image.get_cmap())
        #             im.set_clim(changed_image.get_clim())
        #
        # for im in art:
        #     im.callbacksSM.connect('changed', update)

        # update clim for all plots

        # for the general case where images are non-uniform shape, we have to
        # flatten them all to get the colour percentile values.
        # TODO: will be more eficcient for large number of images to sample
        #  evenly from each image
        pixels = []
        for im in images:
            pixels.extend(im.compressed() if np.ma.isMA(im) else im.ravel())
        pixels = np.array(pixels)

        clim = imd.clim_from_data(pixels, plims=plims)
        imd.sliders.set_positions(clim, draw_on=False)  # no canvas yet!

        # Update histogram with data from all images
        imd.histogram.set_array(pixels)
        imd.histogram.autoscale_view()

    return fig, axes, imd


class FromNameMixin(object):
    @classmethod
    def from_name(cls, method, *args, **kws):
        """
        Construct derived subtype from `method` string and `kws`
        """

        from recipes.iter import itersubclasses

        if not isinstance(method, str):
            raise TypeError('method should be a string.')

        allowed_names = set()
        for sub in itersubclasses(cls.__bases__[0]):
            name = sub.__name__
            if name.lower().startswith(method.lower()):
                break
            else:
                allowed_names.add(name)

        else:
            raise ValueError('Unrecognized method %r. Please use one of '
                             'the following %s' %
                             (method, tuple(allowed_names)))

        return sub(*args, **kws)


class Interval(BaseInterval, FromNameMixin):
    def get_limits(self, values):
        print('hi')  # FIXME: this is missed
        return BaseInterval.get_limits(self, _sanitize_data(values))


class Stretch(BaseStretch, FromNameMixin):
    pass


# class ImageNormalize(mpl_normalize.ImageNormalize):
#
#     # FIXME: ImageNormalize fills masked arrays with vmax instead of removing
# them.
#     # this skews the colour distribution.  TODO: report bug
#
#     def __init__(self, data=None, *args, **kws):
#         if data is not None:
#             data = _sanitize_data(data)
#
#         mpl_normalize.ImageNormalize.__init__(self, data, *args, **kws)
#
#     def __call__(self, values, clip=None):
#         return mpl_normalize.Normalize.__call__(
#                 self, _sanitize_data(values), clip)

from recipes.misc import duplicate_if_scalar


class ColourBarHistogram(LoggingMixin):  # PixelHistogram
    """
    Histogram of colour values in an image
    """

    # TODO: get to work with RGB data

    _default_n_bins = 50

    @classmethod
    def from_image(cls, image_artist):
        pass

    # todo. better with data?
    def __init__(self, ax, image_plot, orientation='horizontal', use_blit=True,
                 outside_colour=None, outside_alpha=0.5, **kws):
        """
        Display a histogram for colour values in an image.

        Parameters
        ----------
        ax
        image_plot
        use_blit
        outside_colour
        kws
        """

        # TODO: dynamic recompute histogram when too few bins are shown..

        # TODO: integrate color stretch functionality
        #  FIXME: fails for all zero data

        from matplotlib.collections import PolyCollection
        from matplotlib.colors import ListedColormap

        self.log = kws.setdefault('log', True)
        self.ax = ax
        self.image_plot = image_plot
        self.norm = image_plot.norm

        assert orientation.lower().startswith(('h', 'v'))
        self.orientation = orientation

        # setup colormap
        cmap = image_plot.get_cmap()
        colors = cmap(np.linspace(0, 1, 256))
        self.cmap = ListedColormap(colors)

        # optionally gray out out-of-bounds values
        if outside_colour is None:
            outside_colours = self.cmap([0., 1.])  # note float
            outside_colours[:, -1] = outside_alpha
            under, over = outside_colours
        else:
            under, over = duplicate_if_scalar(outside_colour)
        #
        self.cmap.set_over(over)
        self.cmap.set_under(under)

        # compute histogram
        self.bins = self.counts = self.bin_edges = self.bin_centers = ()
        self.compute(self.get_array())

        # create collection
        self.bars = PolyCollection(self.get_verts(self.counts, self.bin_edges),
                                   array=self.norm(self.bin_centers),
                                   cmap=self.cmap)
        ax.add_collection(self.bars)

        if use_blit:
            # image_plot.set_animated(True)
            self.bars.set_animated(True)

        # set axes limits
        if self.log:
            ax.set_xscale('log')

        # rescale if non-empty histogram
        if len(self.counts):
            self.autoscale_view()

    def get_array(self):
        return self.image_plot.get_array()

    def set_array(self, data):

        # compute histogram
        self.compute(data)

        # create collection
        self.bars.set_verts(self.get_verts(self.counts, self.bin_edges))
        self.bars.set_array(self.norm(self.bin_centers))

    def compute(self, data, bins=_default_n_bins, range=None):

        # compute histogram
        self.bins = self._auto_bins(bins)  # TODO: allow passing
        if range is None:
            range = self._auto_range()

        self.counts, self.bin_edges = \
            np.histogram(_sanitize_data(data), self.bins, range)
        self.bin_centers = self.bin_edges[:-1] + np.diff(self.bin_edges)

    def get_verts(self, counts, bin_edges):
        """vertices for horizontal bars"""
        # FIXME: order swaps for vertical bars

        if len(counts) == 0:
            # empty histogram
            return []

        xmin = 0
        ywidth = np.diff(bin_edges[:2])[0]
        return [[(xmin, ymin),
                 (xmin, ymin + ywidth),
                 (xmin + xwidth, ymin + ywidth),
                 (xmin + xwidth, ymin),
                 (xmin, ymin)]
                for xwidth, ymin in zip(counts, bin_edges)]

    def update(self):

        # data = self.image_plot.get_array()
        # rng = self._auto_range()
        #
        # self.counts, self.bin_edges = counts, bin_edges =\
        #     np.histogram(_sanitize_data(data), self.bins, rng)

        # verts = self.get_verts(counts, bin_edges)
        # self.bars.set_verts(verts)
        #
        # bin_centers = bin_edges[:-1] + np.diff(bin_edges)
        # self.bars.set_array(self.norm(self.bin_centers))
        # note set_array doesn't seem to work correctly. bars outside the
        #  range get coloured for some reason

        self.bars.set_facecolors(self.cmap(self.norm(self.bin_centers)))
        return self.bars  # TODO: xtick labels if necessary

    def _auto_bins(self, n=_default_n_bins):
        bins = n
        data = self.get_array()

        # unit bins for integer arrays containing small range of numbers
        if data.dtype.kind == 'i':  # integer array
            lo, hi = np.nanmin(data), np.nanmax(data)
            bins = np.arange(min(hi - lo, n) + 1)
        return bins

    def _auto_range(self, stretch=1.2):
        # choose range based on image colour limits
        vmin, vmax = self.image_plot.get_clim()
        if vmin == vmax:
            self.logger.warning('Colour range is 0! Falling back to min-max '
                                'range.')
            image = self.get_array()
            return image.min(), image.max()

        # set the axes limits slightly wider than the clims
        m = 0.5 * (vmin + vmax)
        w = (vmax - vmin) * stretch
        return m - w / 2, m + w / 2

    def autoscale_view(self):

        # set the axes limits slightly wider than the clims
        # the naming here assumes horizontal histogram orientation
        xmin = 0.1 if self.log else 0
        xlim = (xmin, self.counts.max())
        ylim = self._auto_range()

        if self.orientation.startswith('v'):
            xlim, ylim = ylim, xlim

        # self.logger.debug('Ax lims: (%.1f, %.1f)', *lim)
        self.ax.set(xlim=xlim, ylim=ylim)


# ****************************************************************************************************
class ImageDisplay(LoggingMixin):
    # TODO: move cursor with arrow keys when hovering over figure (like ds9)
    # TODO: optional zoomed image window

    # FIXME: Dragging too slow for large images: option for update on release
    #  instead of update on drag!!

    # TODO: optional Show which region on the histogram corresponds to colorbar
    # TODO: better histogram for integer / binary data with narrow ranges
    # TODO: method pixels corresponding to histogram bin?

    # TODO: remove ticks on cbar ax
    # TODO: plot scale func on hist axis

    sliderClass = TripleSliders  # AxesSliders
    _default_plims = (0.25, 99.75)
    _default_hist_kws = dict(bins=100)

    def __init__(self, image, *args, **kws):
        """

        Parameters
        ----------
        image
        args
        kws


        Keywords
        --------
        cbar
        cax
        hist
        hax
        sliders
        ax

        remaining keywords passed to ax.imshow


        """
        # ax      :       Axes object
        #     Axes on which to display

        self.has_cbar = kws.pop('cbar', True)
        hist_kws = kws.pop('hist', self._default_hist_kws)
        if hist_kws is True:
            hist_kws = self._default_hist_kws
        self.has_hist = bool(hist_kws)
        self.has_sliders = kws.pop('sliders', True)
        self.use_blit = kws.pop('use_blit', False)
        connect = kws.pop('connect', self.has_sliders)
        # set origin
        kws.setdefault('origin', 'lower')

        # check data
        image = np.ma.asarray(image).squeeze()  # remove redundant dimensions
        if image.ndim != 2:
            msg = f'{self.__class__.__name__} cannot image {image.ndim}D data.'
            if image.ndim == 3:
                msg += 'Use `VideoDisplay` class to image 3D data.'
            raise ValueError(msg)

        # convert boolean to integer (for colour scale algorithm)
        if image.dtype.name == 'bool':
            image = image.astype(int)

        self.data = image
        self.ishape = self.data.shape

        # create the figure if needed
        self.divider = None
        self.figure, axes = self.init_figure(kws)
        self.ax, self.cax, self.hax = axes
        ax = self.ax

        # use imshow to do the plotting
        self.clim_from_data(image, kws)

        self.imagePlot = ax.imshow(image, *args, **kws)
        self.norm = self.imagePlot.norm
        # self.imagePlot.set_clim(*clim)

        # create the colourbar / histogram / sliders
        self.cbar = None
        if self.has_cbar:
            self.cbar = self.make_cbar()

        # create sliders after histogram so they display on top
        self.sliders, self.histogram = self.make_sliders(hist_kws)

        # connect on_draw for debugging
        self._draw_count = 0
        # self.cid = ax.figure.canvas.mpl_connect('draw_event', self._on_draw)

        if connect:
            self.connect()

    # def _clean_kws(self):
    #     s = inspect.signature(self.ax.imshow)
    #     set(s.parameters.keys()) - {'X', 'data', 'kwargs'}

    def init_figure(self, kws):
        """
        Create the figure and add the axes

        Parameters
        ----------
        kws

        Returns
        -------

        """
        # note intentionally not unpacking keyword dict so that the keys
        #  removed here reflect at the calling scope
        ax = kws.pop('ax', None)
        cax = kws.pop('cax', None)
        hax = kws.pop('hax', None)
        title = kws.pop('title', None)
        autosize = kws.pop('autosize', True)
        # sidebar = kws.pop('sidebar', True)

        # create axes if required
        if ax is None:
            if autosize:
                # automatically determine the figure size based on the data
                figsize = self.guess_figsize(self.data)

                # FIXME: the guessed size does not account for the colorbar
                #  histogram
            else:
                figsize = None

            fig = plt.figure(figsize=figsize)
            self._gs = gs = GridSpec(1, 1,
                                     left=0.05, right=0.95,
                                     top=0.98, bottom=0.05, )
            ax = fig.add_subplot(gs[0, 0])
            ax.tick_params('x', which='both', top=True)

        # axes = namedtuple('AxesContainer', ('image',))(ax)
        if self.has_cbar and (cax is None):
            self.divider = make_axes_locatable(ax)
            cax = self.divider.append_axes('right', size=0.2, pad=0)

        if self.has_hist and (hax is None):
            hax = self.divider.append_axes('right', size=1, pad=0.2)

        # set the axes title if given
        if title is not None:
            ax.set_title(title)

        # setup coordinate display
        ax.format_coord = self.format_coord
        return ax.figure, (ax, cax, hax)

    def guess_figsize(self, data, fill_factor=0.55, max_pixel_size=0.2):
        """
        Make an educated guess of the size of the figure needed to display the
        image data.

        Parameters
        ----------
        data: np.ndarray
            Sample image
        fill_factor: float
            Maximal fraction of screen size allowed in any direction
        max_pixel_size: 2-tuple
            Maximum allowed pixel size (heigh, width) in inches

        Returns
        -------
        size: tuple
            Size (width, height) of the figure in inches


        """

        #
        image = self.data[0] if data is None else data
        return guess_figsize(image, fill_factor, max_pixel_size)

    def make_cbar(self):
        fmt = None
        if self.has_hist:
            # No need for the data labels on the colourbar since it will be on
            # the histogram axis.
            from matplotlib import ticker
            fmt = ticker.NullFormatter()

        # cax = self.axes.cbar
        cbar = self.figure.colorbar(self.imagePlot, cax=self.cax, format=fmt)
        return cbar

    def make_sliders(self, hist_kws=None):
        # data = self.imagePlot.get_array()

        # FIXME: This is causing recursive repaint!!

        sliders = None
        if self.has_sliders:
            clim = self.imagePlot.get_clim()
            sliders = self.sliderClass(self.hax, clim, 'y',
                                       color='rbg',
                                       ms=(2, 1, 2),
                                       extra_markers='>s<')

            sliders.lower.on_move.add(self.update_clim)
            sliders.upper.on_move.add(self.update_clim)

        cbh = None
        if self.has_hist:
            cbh = ColourBarHistogram(self.hax, self.imagePlot, 'horizontal',
                                     self.use_blit, **hist_kws)

            # set ylim if reasonable to do so
            # if data.ptp():
            #     # avoid warnings in setting upper/lower limits identical
            #     hax.set_ylim((data.min(), data.max()))
            # NOTE: will have to change with different orientation

            self.hax.yaxis.tick_right()
            self.hax.grid(True)
        return sliders, cbh

    def clim_from_data(self, data, kws=None, **kws_):
        """Get colour scale limits for data"""
        # first arg is dict from which we remove 'extra' keywords that
        # are not allowed in imshow. This allows a dict friom the calling
        # scope to be edited here without global statement.
        # This function can also still be used with unpacked keyword args
        kws = kws or {}
        clim = kws.pop('clim', True)
        plims = kws.pop('plims', None)
        if clim:
            plims = kws_.setdefault('plims', plims)
            if plims is None:
                kws_['plims'] = self._default_plims

            clims = get_percentile_limits(_sanitize_data(data), **kws_)
            self.logger.debug('Colour limits: (%.1f, %.1f)', *clims)
            kws['vmin'], kws['vmax'] = clims
            return clims
        return None, None

    def set_clim(self, *clim):
        self.imagePlot.set_clim(*clim)

        if not self.has_hist:
            return self.imagePlot

        self.histogram.update()
        self.sliders.min_span = (clim[0] - clim[1]) / 100

        # TODO: return COLOURBAR ticklabels?
        return self.imagePlot, self.histogram.bars

    def update_clim(self, *xydata):
        """Set colour limits on slider move"""
        return self.set_clim(*self.sliders.positions)

    def _on_draw(self, event):
        self.logger.debug('DRAW %i', self._draw_count)  # ,  vars(event)
        if self._draw_count == 0:
            self._on_first_draw(event)
        self._draw_count += 1

    def _on_first_draw(self, event):
        self.logger.debug('FIRST DRAW')

    def format_coord(self, x, y, precision=3, masked_str='masked'):
        """
        Create string representation for cursor position in data coordinates

        Parameters
        ----------
        x: float
            x data coordinate position
        y: float
            y data coordinate position
        precision: int, optional
            decimal precision for string format
        masked_str: str, optional
            representation for masked elements in array
        Returns
        -------
        str
        """

        # MASKED_STR = 'masked'

        # xy repr
        xs = 'x=%1.{:d}f'.format(precision) % x
        ys = 'y=%1.{:d}f'.format(precision) % y
        # z
        col, row = int(x + 0.5), int(y + 0.5)
        nrows, ncols = self.ishape
        if (0 <= col < ncols) and (0 <= row < nrows):
            data = self.imagePlot.get_array()
            z = data[row, col]
            # handle masked data
            if np.ma.is_masked(z):
                # prevent Warning: converting a masked element to nan.
                zs = 'z=%s' % masked_str
            else:
                zs = 'z=%1.{:d}f'.format(precision) % z
            return ',\t'.join((xs, ys, zs)).expandtabs()
        else:
            return ', '.join((xs, ys))

    def connect(self):
        if self.sliders is None:
            return

        # connect sliders' interactions + save background for blitting
        self.sliders.connect()


class AstroImageDisplay(ImageDisplay):

    def clim_from_data(self, data, kws):
        # colour transform / normalize
        interval = kws.pop('interval', 'zscale')
        stretch = kws.pop('stretch', 'linear')

        # note: ImageNormalize fills masked values.. this is clearly WRONG
        #  since it will skew statistics on the image. important that data
        #  passed to this function has been cleaned of masked values
        # HACK: get limits ignoring masked pixels
        #         # set the slider positions / color limits

        self.norm = get_norm(data, interval, stretch)
        # kws['norm'] = norm
        clim = self.norm.interval.get_limits(data)
        return clim


# ****************************************************************************************************
class VideoDisplay(ImageDisplay):
    # FIXME: blitting not working - something is leading to auto draw
    # FIXME: frame slider bar not drawing on blit
    # TODO: lock the sliders in place with button??

    _scroll_wrap = True  # scrolling past the end leads to the beginning

    def __init__(self, data, **kws):
        """
        Image display for 3D data. Implements frame slider and image scroll.

        subclasses optionally implement `update` method

        Parameters
        ----------
        data:       np.ndarray or np.memmap
            initial display data

        clim_every: int
            How frequently to re-run the color normalizer algorithm to set
            the colour limits. Setting this to `False` may have a positive
            effect on performance.

        kws are passed directly to ImageDisplay.
        """

        if not isinstance(data, np.ndarray):
            data = np.ma.asarray(data)

        # setup image display
        n = self._frame = 0

        n_dim = data.ndim
        if n_dim == 2:
            warnings.warn('Loading single image frame as 3D data cube. Use '
                          '`ImageDisplay` instead to view single frames.')
            data = np.ma.atleast_3d(data)

        if n_dim != 3:
            raise ValueError('Cannot image %iD data' % n_dim)

        #
        self.clim_every = kws.pop('clim_every', 1)

        # don't connect methods yet
        connect = kws.pop('connect', True)

        # parent sets data as 2D image.
        ImageDisplay.__init__(self, data[n], connect=False, **kws)
        # save data (this can be array_like (or np.mmap))
        self.data = data

        # make observer container for scroll
        # self.on_scroll = Observers()

        # make frame slider
        fsax = self.divider.append_axes('bottom', size=0.1, pad=0.3)
        self.frameSlider = Slider(fsax, 'frame', n, len(data), valfmt='%d')
        self.frameSlider.on_move(self.update)
        fsax.xaxis.set_major_locator(ticker.AutoLocator())

        if self.use_blit:
            self.frameSlider.drawon = False

        # # save background for blitting
        # self.background = self.figure.canvas.copy_from_bbox(
        #     self.ax.bbox)

        if connect:
            self.connect()

    def connect(self):
        ImageDisplay.connect(self)

        # enable frame scroll
        self.figure.canvas.mpl_connect('scroll_event', self._scroll)

    # def init_figure(self, **kws):
    #     fig, ax = ImageDisplay.init_figure(self, **kws)
    #     return fig, ax

    # def init_axes(self, fig):
    #     gs = GridSpec(100, 100,
    #                   left=0.05, right=0.95,
    #                   top=0.98, bottom=0.05,
    #                   hspace=0, wspace=0)
    #     q = 97
    #     return self._init_axes(fig,
    #                            image=gs[:q, :80],
    #                            cbar=gs[:q, 80:85],
    #                            hbar=gs[:q, 87:],
    #                            fslide=gs[q:, :80])

    def guess_figsize(self, data, fill_factor=0.55, max_pixel_size=0.2):
        # TODO: inherit docstring
        size = super().guess_figsize(data, fill_factor, max_pixel_size)
        # create a bit more space below the figure for the frame nr indicator
        size[1] += 0.5
        self.logger.debug('Guessed figure size: (%.1f, %.1f)', *size)
        return size

    @property
    def frame(self):
        """Index of image currently being displayed"""
        return self._frame

    @frame.setter
    def frame(self, i):
        """Set frame data respecting scroll wrap"""
        self.set_frame(i)

    def set_frame(self, i):
        """
        Set frame data respecting scroll wrap
        """
        # wrap scrolling if desired
        if self._scroll_wrap:
            # wrap around! scroll past end ==> go to beginning
            i %= len(self.data)
        else:  # stop scrolling at the end
            i = max(i, len(self.data))

        i = int(round(i, 0))  # make sure we have an int
        self._frame = i  # store current frame

    def get_image_data(self, i):
        """
        Get the image data to be displayed.

        Parameters
        ----------
        i: int
            Frame number

        Returns
        -------
        np.ndarray

        """
        return self.data[i]

    def update(self, i, draw=True):
        """
        Display the image associated with index `i` frame in the sequence. This
        method can be over-written to change image switching behaviour
        in subclasses.

        Parameters
        ----------
        i: int
            Frame index
        draw: bool
            Whether the canvas should be redrawn after the data is updated


        Returns
        -------
        draw_list: list
            list of artists that have been changed and need to be redrawn
        """
        self.set_frame(i)

        image = self.get_image_data(self.frame)
        # set the image data
        # TODO: method set_image_data here??
        self.imagePlot.set_data(image)  # does not update normalization

        # FIXME: normalizer fails with boolean data
        #  File "/usr/local/lib/python3.6/dist-packages/matplotlib/colorbar.py", line 956, in on_mappable_changed
        #   self.update_normal(mappable)
        # File "/usr/local/lib/python3.6/dist-packages/matplotlib/colorbar.py", line 987, in update_normal

        draw_list = [self.imagePlot]

        # set the slider axis limits
        if self.sliders:
            # find min / max as float
            imin, imax = float(np.nanmin(image)), float(np.nanmax(image))
            self.sliders.ax.set_ylim(imin, imax)
            self.sliders.valmin, self.sliders.valmax = imin, imax
            # since we changed the axis limits, need to redraw the tick labels
            getter = getattr(self.histogram.ax,
                             'get_%sticklabels' % self.sliders.slide_axis)
            draw_list.extend(getter())

        # update histogram
        if self.has_hist:
            draw_list.extend(self.histogram.update(image))

        if not (self._draw_count % self.clim_every):
            # set the slider positions / color limits
            # TODO: move to clim_from_data

            if getattr(self.norm, 'interval', None):
                vmin, vmax = self.norm.interval.get_limits(
                        _sanitize_data(image))
                bad_clims = (vmin == vmax)
                if bad_clims:
                    # self.logger.warning('Bad colour interval from %s: '
                    #                     '(%.1f, %.1f). Ignoring',
                    #                     self.imagePlot.norm.interval.__class__,
                    #                     vmin, vmax)
                    self.logger.warning('Bad colour interval: '
                                        '(%.1f, %.1f). Ignoring',
                                        vmin, vmax)
                else:
                    self.logger.debug('Auto clims: (%.1f, %.1f)', vmin, vmax)
                    self.imagePlot.set_clim(vmin, vmax)

                    if self.sliders:
                        draw_list = self.sliders.set_positions((vmin, vmax),
                                                               draw_on=False)

                    # set the axes limits slightly wider than the clims
                    if self.has_hist:
                        self.histogram.autoscale_view()

        #
        if draw:
            self.sliders.draw(draw_list)

        return draw_list
        # return i, image

    def _scroll(self, event):

        # FIXME: drawing on scroll.....
        # try:
        inc = [-1, +1][event.button == 'up']
        new = self._frame + inc
        if self.use_blit:
            self.frameSlider.drawon = False
        self.frameSlider.set_val(new)  # calls connected `update`
        self.frameSlider.drawon = True

        # except Exception as err:
        #     self.logger.exception('Scroll failed:')

    def play(self, start=None, stop=None, pause=0):
        """
        Show a video of images in the model space

        Parameters
        ----------
        n: int
            number of frames in the animation
        pause: int
            interval between frames in milliseconds

        Returns
        -------

        """

        if stop is None and start:
            stop = start
            start = 0
        if start is None:
            start = 0
        if stop is None:
            stop = len(self.data)

        # save background for blitting
        # FIXME: saved bg should be without
        tmp_inviz = [self.frameSlider.poly, self.frameSlider.valtext]
        # tmp_inviz.extend(self.histogram.ax.yaxis.get_ticklabels())
        tmp_inviz.append(self.histogram.bars)
        for s in tmp_inviz:
            s.set_visible(False)

        fig = self.figure
        fig.canvas.draw()
        self.background = fig.canvas.copy_from_bbox(self.figure.bbox)

        for s in tmp_inviz:
            s.set_visible(True)

        self.frameSlider.eventson = False
        self.frameSlider.drawon = False

        # pause: inter-frame pause (millisecond)
        seconds = pause / 1000
        i = int(start)

        # note: the fastest frame rate achievable currently seems to be
        #  around 20 fps
        try:
            while i <= stop:
                self.frameSlider.set_val(i)
                draw_list = self.update(i)
                draw_list.extend([self.frameSlider.poly,
                                  self.frameSlider.valtext])

                fig.canvas.restore_region(self.background)

                # FIXME: self.frameSlider.valtext doesn't dissappear on blit

                for art in draw_list:
                    self.ax.draw_artist(art)

                fig.canvas.blit(fig.bbox)

                i += 1
                time.sleep(seconds)
        except Exception as err:
            raise err
        finally:
            self.frameSlider.eventson = True
            self.frameSlider.drawon = True

    # def blit_setup(self):

    # @expose.args()
    # def draw_blit(self, artists):
    #
    #     self.logger.debug('draw_blit')
    #
    #     fig = self.figure
    #     fig.canvas.restore_region(self.background)
    #
    #     for art in artists:
    #         try:
    #             self.ax.draw_artist(art)
    #         except Exception as err:
    #             self.logger.debug('drawing FAILED %s', art)
    #             traceback.print_exc()
    #
    #     fig.canvas.blit(fig.bbox)

    # def format_coord(self, x, y):
    #     s = ImageDisplay.format_coord(self, x, y)
    #     return 'frame %d: %s' % (self.frame, s)

    # def format_coord(self, x, y):
    #     col, row = int(x + 0.5), int(y + 0.5)
    #     nrows, ncols, _ = self.data.shape
    #     if (col >= 0 and col < ncols) and (row >= 0 and row < nrows):
    #         z = self.data[self._frame][row, col]
    #         return 'x=%1.3f,\ty=%1.3f,\tz=%1.3f' % (x, y, z)
    #     else:
    #         return 'x=%1.3f, y=%1.3f' % (x, y)


# ****************************************************************************************************
class VideoDisplayX(VideoDisplay):
    # FIXME: redraw markers after color adjust
    # TODO: improve memory performance by allowing coords to update via func

    marker_properties = dict(c='r', marker='x', alpha=1, ls='none', ms=5)

    def __init__(self, data, coords=None, **kws):
        """

        Parameters
        ----------
        data: array-like
            Image stack. shape (N, ypix, xpix)
        coords:  array_like, optional
            coordinate positions (yx) of apertures to display. This must be
            array_like with
            shape (N, k, 2) where k is the number of apertures per frame, and N
            is the number of frames.
        kws:
            passed to `VideoDisplay`
        """

        VideoDisplay.__init__(self, data, **kws)

        # create markers
        self.marks, = self.ax.plot([], [], **self.marker_properties)

        # check coords
        self.coords = coords
        self.has_coords = (coords is not None)
        if self.has_coords:
            coords = np.asarray(coords)
            if coords.ndim not in (2, 3) or (coords.shape[-1] != 2):
                raise ValueError('Coordinate array has incorrect shape: %s',
                                 coords.shape)
            if coords.ndim == 2:
                # Assuming single coordinate point per frame
                coords = coords[:, None]
            if len(coords) < len(data):
                self.logger.warning(
                        'Coordinate array contains fewer points (%i) than '
                        'the number of frames (%i).', len(coords), len(data))

            # set for frame 0
            self.marks.set_data(coords[0, :, ::-1].T)
            self.get_coords = self.get_coords_internal

    def get_coords(self, i):
        return

    def get_coords_internal(self, i):
        i = int(round(i))
        return self.coords[i, :, ::-1].T

    def update(self, i, draw=True):
        # self.logger.debug('update')
        # i = round(i)
        draw_list = VideoDisplay.update(self, i, False)
        #
        coo = self.get_coords(i)
        if coo is not None:
            self.marks.set_data(coo)
            draw_list.append(self.marks)

        return draw_list


# ****************************************************************************************************
from obstools.aps import ApertureCollection


class VideoDisplayA(VideoDisplayX):
    # default aperture properties
    apProps = dict(ec='m', lw=1,
                   picker=False,
                   widths=7.5, heights=7.5)

    def __init__(self, data, coords=None, ap_props={}, **kws):
        """
        Optionally also displays apertures if coordinates provided.
        """
        VideoDisplayX.__init__(self, data, coords, **kws)

        # create apertures
        props = VideoDisplayA.apProps.copy()
        props.update(ap_props)
        self.aps = self.create_apertures(**props)

    def create_apertures(self, **props):
        props.setdefault('animated', self.use_blit)
        aps = ApertureCollection(**props)
        # add apertures to axes.  will not display yet if coordinates not given
        aps.add_to_axes(self.ax)
        return aps

    def update_apertures(self, i, *args, **kws):
        coords, *_ = args
        self.aps.coords = coords
        return self.aps

    def update(self, i, draw=True):
        # get all the artists that changed by calling parent update
        draw_list = VideoDisplay.update(self, i, False)
        #
        coo = self.get_coords(i)
        if coo is not None:
            self.marks.set_data(coo)
            draw_list.append(self.marks)

        art = self.update_apertures(i, coo.T)
        draw_list.append(art)

        return draw_list

        # self.ap_updater(self.aps, i)
        # self.aps.coords = coo.T

        # except Exception as err:
        #     self.logger.exception('Aperture update failed at %i', i)
        #     self.aps.coords = np.empty((0, 2))
        # else:
        # draw_list.append(self.aps)
        # finally:
        # return draw_list


# ****************************************************************************************************
class Compare3DImage(LoggingMixin):
    # TODO: profile & speed up!
    # TODO: link viewing angles!!!!!!!!!
    # TODO: blit for view angle change...
    # MODE = 'update'
    """Class for plotting image data for comparison"""

    # @profile()
    def __init__(self, *args, **kws):
        """

        Parameters
        ----------
        args : tuple
            (X, Y, Z, data)  or  (fig, X, Y, Z, data)   or   ()
        kws :
        """

        self.plots = []
        self.images = []
        self.titles = kws.get('titles', ['Data', 'Fit', 'Residual'])
        self._get_clim, kws = get_colour_scaler(**kws)

        nargs = len(args)
        if nargs == 0:
            fig = None
            data = ()
        elif nargs == 4:
            fig = None
            data = args
        elif nargs == 5:
            fig, *data = args
        else:
            raise ValueError('Incorrect number of parameters')

        self.fig = self.setup_figure(fig)
        if len(data):
            # X, Y, Z, data
            self.update(*data)

    # @unhookPyQt
    def setup_figure(self, fig=None):
        # TODO: Option for colorbars
        # TODO:  Include info as text in figure??????
        """
        Initialize grid of 2x3 subplots. Top 3 are 3D wireframe, bottom 3 are colour images of
        data, fit, residual.
        """
        # Plots for current fit
        fig = fig or plt.figure(figsize=(14, 10), )
        # gridpec_kw=dict(left=0.05, right=0.95,
        #                 top=0.98, bottom=0.01))
        if not isinstance(fig, Figure):
            raise ValueError('Expected Figure, received %s' % type(fig))

        self.grid_3D = self.setup_3D_axes(fig)
        self.grid_images = self.setup_image_axes(fig)

        # fig.suptitle('PSF Fitting')    # NOTE:  Does not display correctly with tight layout
        return fig

    def setup_3D_axes(self, fig):
        # Create the plot grid for the 3D plots
        grid_3D = AxesGrid(fig, 211,  # similar to subplot(211)
                           nrows_ncols=(1, 3),
                           axes_pad=-0.2,
                           label_mode=None,
                           # This is necessary to avoid AxesGrid._tick_only
                           # throwing
                           share_all=True,
                           axes_class=(Axes3D, {}))

        for ax, title in zip(grid_3D, self.titles):
            # pl = ax.plot_wireframe([],[],[])
            # since matplotlib 1.5 can no longer initialize this way
            pl = Line3DCollection([])
            ax.add_collection(pl)

            # set title to display above axes
            title = ax.set_title(title, dict(fontweight='bold',
                                             fontsize=14))
            x, y = title.get_position()
            title.set_position((x, 1.0))
            ax.set_facecolor('None')
            # ax.patch.set_linewidth( 1 )
            # ax.patch.set_edgecolor( 'k' )
            self.plots.append(pl)

        return grid_3D

    def setup_image_axes(self, fig):
        # Create the plot grid for the images
        grid_images = AxesGrid(fig, 212,  # similar to subplot(212)
                               nrows_ncols=(1, 3),
                               axes_pad=0.1,
                               label_mode='L',  # THIS DOESN'T FUCKING WORK!
                               # share_all = True,
                               cbar_location='right',
                               cbar_mode='each',
                               cbar_size='7.5%',
                               cbar_pad='0%')

        for i, (ax, cax) in enumerate(zip(grid_images, grid_images.cbar_axes)):
            im = ax.imshow(np.zeros((1, 1)), origin='lower')
            with warnings.catch_warnings():
                # UserWarning: Attempting to set identical bottom==top resultsin singular transformations; automatically expanding.
                warnings.filterwarnings('ignore', category=UserWarning)
                cbar = cax.colorbar(im)

            # make the colorbar ticks look nice
            c = 'orangered'  # > '0.85'
            cax.axes.tick_params(axis='y',
                                 pad=-7,
                                 direction='in',
                                 length=3,
                                 colors=c,
                                 labelsize='x-small')
            # make the colorbar spine invisible
            cax.spines['left'].set_visible(False)
            # for w in ('top', 'bottom', 'right'):
            cax.spines['right'].set_color(c)

            for t in cax.axes.yaxis.get_ticklabels():
                t.set_weight('bold')
                t.set_ha('center')
                t.set_va('center')
                t.set_rotation(90)

                # if i>1:
                # ax.set_yticklabels( [] )       #FIXME:  This kills all ticklabels
            self.images.append(im)

        return grid_images

    @staticmethod
    def make_segments(X, Y, Z):
        """Update segments of wireframe plots."""
        # NOTE: Does not seem to play well with masked data - mask shape changes...
        xlines = np.r_['-1,3,0', X, Y, Z]
        ylines = xlines.transpose(1, 0, 2)  # swap x-y axes
        return list(xlines) + list(ylines)

    def get_clim(self, data):
        data = _sanitize_data(data)
        return self._get_clim(data)

    def update(self, X, Y, Z, data):
        """update plots with new data."""

        res = data - Z
        plots, images = self.plots, self.images
        # NOTE: mask shape changes, which breaks things below.
        plots[0].set_segments(self.make_segments(X, Y, data.copy()))
        plots[1].set_segments(self.make_segments(X, Y, Z))
        plots[2].set_segments(self.make_segments(X, Y, res.copy()))
        images[0].set_data(data)
        images[1].set_data(Z)
        images[2].set_data(res)

        zlims = [Z.min(), Z.max()]
        rlims = [res.min(), res.max()]
        clims = self.get_clim(data)
        # plims = 0.25, 99.75                             #percentiles
        # clims = np.percentile( data, plims )            #colour limits for data
        # rlims = np.percentile( res, plims )             #colour limits for residuals
        for i, pl in enumerate(plots):
            ax = pl.axes
            ax.set_zlim(zlims if (i + 1) % 3 else rlims)

        xr = X[0, [0, -1]]
        yr = Y[[0, -1], 0]
        with warnings.catch_warnings():
            # filter `UserWarning: Attempting to set identical bottom==top resultsin singular transformations; automatically expanding.`
            warnings.filterwarnings("ignore", category=UserWarning)
            ax.set_xlim(xr)
            ax.set_ylim(yr)

            # artificially set axes limits --> applies to all since share_all=True in constructor
            for i, im in enumerate(images):
                lims = clims if (i + 1) % 3 else rlims
                im.set_clim(lims)
                im.set_extent(np.r_[xr, yr])

                # self.fig.canvas.draw()
                # TODO: SAVE FIGURES.................


# ****************************************************************************************************
class Compare3DContours(Compare3DImage):
    def setup_image_axes(self, fig):
        # Create the plot grid for the contour plots
        self.grid_contours = AxesGrid(fig, 212,  # similar to subplot(211)
                                      nrows_ncols=(1, 3),
                                      axes_pad=0.2,
                                      label_mode='L',
                                      # This is necessary to avoid AxesGrid._tick_only throwing
                                      share_all=True)

    def update(self, X, Y, Z, data):
        """update plots with new data."""
        res = data - Z
        plots, images = self.plots, self.images

        plots[0].set_segments(self.make_segments(X, Y, data))
        plots[1].set_segments(self.make_segments(X, Y, Z))
        plots[2].set_segments(self.make_segments(X, Y, res))
        # images[0].set_data( data )
        # images[1].set_data( Z )
        # images[2].set_data( res )

        for ax, z in zip(self.grid_contours, (data, Z, res)):
            cs = ax.contour(X, Y, z)
            ax.clabel(cs, inline=1, fontsize=7)  # manual=manual_locations

        zlims = [Z.min(), Z.max()]
        rlims = [res.min(), res.max()]
        # plims = 0.25, 99.75                             #percentiles
        # clims = np.percentile( data, plims )            #colour limits for data
        # rlims = np.percentile( res, plims )             #colour limits for residuals
        for i, pl in enumerate(plots):
            ax = pl.axes
            ax.set_zlim(zlims if (i + 1) % 3 else rlims)
        ax.set_xlim([X[0, 0], X[0, -1]])
        ax.set_ylim([Y[0, 0], Y[-1, 0]])

        # for i,im in enumerate(images):
        # ax = im.axes
        # im.set_clim( zlims if (i+1)%3 else rlims )
        ##artificially set axes limits --> applies to all since share_all=True in constuctor
        # im.set_extent( [X[0,0], X[0,-1], Y[0,0], Y[-1,0]] )

        # self.fig.canvas.draw()


# from recipes.array import ndgrid
from recipes.array.neighbours import neighbours


class PSFPlotter(Compare3DImage, VideoDisplay):
    def __init__(self, filename, model, params, coords, window, **kws):
        self.model = model
        self.params = params
        self.coords = coords
        self.window = w = int(window)
        self.grid = np.mgrid[:w, :w]
        extent = np.array([0, w, 0, w]) - 0.5  # l, r, b, t

        Compare3DImage.__init__(self)
        axData = self.grid_images[0]

        FitsCubeDisplay.__init__(self, filename, ax=axData, extent=extent,
                                 sidebar=False, autosize=False)
        self.update(0)  # FIXME: full frame drawn instead of zoom
        # have to draw here for some bizarre reason
        # self.grid_images[0].draw(self.fig._cachedRenderer)

    def get_image_data(self, i):
        # coo = self.coords[i]
        data = neighbours(self[i], self.coords[i], self.window)
        return data

    def update(self, i, draw=False):
        """Set frame data. draw if requested """
        i %= len(self)  # wrap around! (eg. scroll past end ==> go to beginning)
        i = int(round(i, 0))  # make sure we have an int
        self._frame = i  # store current frame

        image = self.get_image_data(i)
        p = self.params[i]
        Z = self.model(p, self.grid)
        Y, X = self.grid
        self.update(X, Y, Z, image)

        if draw:
            self.fig.canvas.draw()

        return i
