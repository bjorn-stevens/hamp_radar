from dataclasses import dataclass
from typing import List, Optional
from collections.abc import Iterable

import numpy as np
import xarray as xr
from numpy.lib.stride_tricks import as_strided


@dataclass
class SingleSubBlockGeometry:
    """
    Representation of a single sub-block, as explained in
    Meteorological Ka-Band Cloud Radar MIRA35 Manual, section 2.3.1
    'Definition of chunk common structure', e.g. for a chunk in an
    Embedded chain type 2 (see section 2.3.3.2).

    Attributes:
        tag (str): The tag/signature representing the chunk type.
        offset (int): Pointer to start of sub-block's data block.
        size (int): The size of the sub-block's data block (bytes).
    """
    tag: str
    offset: int
    size: int


@dataclass
class SingleMainBlockGeometry:
    """
    Representation of a single main block as list of
    SingleSubBlockGeometry instances with some overarching metadata.

    This represents one 'main chunk' as in Meteorological Ka-Band Cloud Radar
    MIRA35 Manual, section 2.3.2 'File structure', and should be chunk as in
    section 2.3.3 'Main chunk structure', that is either for DSP parameters
    as in 'Embedded chain type 1; PPar' or for data frame as in
    'Embedded chain type 2; Data chain'

    The sub-blocks of a SingleMainBlockGeometry correspond to each of the blocks
    in the embedded chunk chain which composes the main chunk.

    Attributes:
        tag (str): The tag / frame header describing the data in the sub-blocks.
        offset (int): Pointer to start of the main block's sub-blocks.
        size (int): The size of the main block's sub-blocks (bytes).
        subblocks (List[SingleSubBlockGeometry]): A list of sub-blocks within
                                                  the main block.
    """
    tag: str
    offset: int
    size: int
    subblocks: List[SingleSubBlockGeometry]


@dataclass
class MultiMainBlockGeometry:
    """
    Representation of multiple main blocks as list of SingleSubBlockGeometry
    instances with some overarching metadata. This can be multiple 'main chunks'
    as in Meteorological Ka-Band Cloud Radar MIRA35 Manual, section 2.3.2
    'File structure'.
    
    Allows for 'count' number of SingleMainBlockGeometry instances to be
    combined, assuming they are similar enough (e.g. same subblocks, same offset,
    same tag etc.). (See compact_geometry function.)

    Attributes:
        tag (str): The tag/signature describing the main blocks.
        offset (int): Pointer to the start of the first main block.
        count (int): The number of SingleMainBlockGeometry instances
                     combined to make the MultiMainBlockGeometry instance.
        step (Optional[int]): The distance between each SingleMainBlockGeometry
                              instance, required if count > 1.
        subblocks (List[SingleSubBlockGeometry]): The sub-blocks in each main block.
    """
    tag: str
    offset: int
    count: int
    step: Optional[int]
    subblocks: List[SingleSubBlockGeometry]


def main_ofs(mainblock):
    # blocktype and blocksize assumed to by 4 bytes long
    """
    Returns the list of SingleSubBlockGeometry instances for a main block of
    data, as in Meteorological Ka-Band Cloud Radar MIRA35 Manual, section 2.3.2
    'File structure'.

    Args:
        mainblock (bytes): The main block data ('main chunk' in manual).

    Returns:
        list: A list of SingleSubBlockGeometry instances, each representing a sub-block
              within the main block.
    """
    o = 0
    ofs = []
    while o + 8 < len(mainblock):
        blocktype = bytes(mainblock[o : o + 4])
        blocksize = mainblock[o + 4 : o + 8].view("<i4")[0]
        ofs.append(SingleSubBlockGeometry(blocktype, o + 8, blocksize))
        o += 8 + blocksize
    return ofs


def get_tag_size(data):
    """
    Extracts the tag (also known as signature) and the pointer for size
    from 'data' assuming layout as in Meteorological Ka-Band Cloud Radar
    MIRA35 Manual, section 2.3.1 'Definition of chunk common structure'
    where the tag is the first 4 bytes of data and the pointer is the
    next 4 bytes.

    Args:
        data (bytes): The data assumed to conform with Meteorological Ka-Band
                      Cloud Radar MIRA35 Manual, section 2.3.1 'Definition of
                      chunk common structure'

    Returns:
        Tuple[bytes, int]: The tag and the pointer for size.
    """
    return bytes(data[:4]), data[4:8].view("<i4")[0]


def get_geometry(data):
    """
    Interprets and returns the geometry of the 'data' assuming it is a
    memmap of a PDS file (or an open PDS file) for radar IQ data with structure
    as in Meteorological Ka-Band Cloud Radar MIRA35 Manual, section 2.3.2
    'File structure'.

    First attempt will read the main tag and size from first 8 bytes
    of the data. If the data length is insufficient, or the main tag is not
    found by attempting to return to the start of the file from main_size+8,
    it shifts the offset by 1024 bytes (to skip file header) and tries again.
    If the main tag is still not found, it raises a ValueError.

    If get_tag_size is sucessful, the function defines a generator
    `main_blocks` that iterates through the data, yielding
    `SingleMainBlockGeometry` instances for each block (i.e. for each main chunk
    as defined in section 2.3.2)

    The function returns a list of different MultiMainBlockGeometry instances
    obtained from compacting together simliar SingleMainBlockGeometry from the
    main_blocks multiple SingleMainBlockGeometry instances.

    Parameters:
    data (bytes): The binary data from which to extract geometry information.

    Returns:
    list: A list of compacted geometry information extracted from the data.

    Raises:
    ValueError: If the main tag cannot be found in the data, indicating that the data may not be a PDS file.
    """
    o = 0
    main_tag, main_size = get_tag_size(data)

    if (
        len(data) < main_size + 8
        or get_tag_size(data[o + 8 + main_size :])[0] != main_tag
    ):
        o = 1024
        main_tag, main_size = get_tag_size(data[o:])
        if get_tag_size(data[o + 8 + main_size :])[0] != main_tag:
            raise ValueError("Could not find main tag, is this a PDS file?")

    def main_blocks(data, o):
        while o + 8 < len(data):
            tag, size = get_tag_size(data[o:])
            yield SingleMainBlockGeometry(
                tag, o + 8, size, main_ofs(data[o + 8 : o + 8 + size])
            )
            o += 8 + size

    return list(compact_geometry(main_blocks(data, o)))


def compact_geometry(
    main_blocks: Iterable[SingleMainBlockGeometry],
) -> Iterable[MultiMainBlockGeometry]:
    """
    Compacts a sequence of SingleMainBlockGeometry instances into a sequence of
    MultiMainBlockGeometry instances.

    This function takes an iterable of SingleMainBlockGeometry objects and
    combines sequential ones that are similar enough ('compatible') into a
    single MultiMainBlockGeometry instance. When the previous
    SingleMainBlockGeometry instance is not compatible to the current one, a new
    MultiMainBlockGeometry instance is started. A list of all the 
    MultiMainBlockGeometry instances is then returned.

    Args:
        main_blocks (Iterable[SingleMainBlockGeometry]): An iterable of
                                                         SingleMainBlockGeometry
                                                         instances.

    Yields:
        Iterable[MultiMainBlockGeometry]: An iterable of MultiMainBlockGeometry
                                          instances.
    """
    base_offset = None
    prev_offset = None
    prev_distance = None
    prev_subblocks = None
    prev_tag = None
    count = 0
    for mb in main_blocks:
        is_compatible = True
        if prev_offset is not None:
            distance = mb.offset - prev_offset
            if prev_distance is not None and prev_distance != distance:
                is_compatible = False
        else:
            distance = None

        if prev_subblocks != mb.subblocks:
            is_compatible = False

        if prev_tag != mb.tag:
            is_compatible = False

        if is_compatible:
            prev_distance = distance
            count += 1
        else:
            if base_offset is not None and prev_subblocks is not None and count > 0:
                yield MultiMainBlockGeometry(
                    prev_tag, base_offset, count, prev_distance, prev_subblocks
                )
            base_offset = mb.offset
            prev_distance = None
            count = 1

        prev_offset = mb.offset
        prev_subblocks = mb.subblocks
        prev_tag = mb.tag

    yield MultiMainBlockGeometry(
        prev_tag, base_offset, count, prev_distance, prev_subblocks
    )


def extract_raw_arrays(data, mmbgs: Iterable[MultiMainBlockGeometry]):
    """
    Generator function to extract arrays from 'data' based on the
    geometry given by the iterator over MultiMainBlockGeometry instances.

    Iterating over a list of this generator yields a tuple for the subblocks
    across all the MultiMainBlockGeometry instances sequentially.

    Optimisation uses NumPy library as_strided function to create a view of the
    original data array interpreted with different shape and strides. Shape will
    have (nrows, ncols) = (mmbg.count, block.size). Stride will be 1 unless
    mmbg.count > 1, in which case mmbg.step is used to advance to the next
    required subblock (skipping past other subblocks with different tags)

    Args:
        data: The input data (memory map or open file) from which to extract the
              arrays.
        mmbgs (Iterable[MultiMainBlockGeometry]): An iterable of
              MultiMainBlockGeometry instances.

    Yields:
        tuple: A tuple containing:
            - mmbg.tag: The tag/signature of the main block.
            - block.tag: The tag/signature of the subblock.
            - ndarray: A view of the data array corresponding to the subblock,
                       created using numpy's as_strided function.
    """
    for mmbg in mmbgs:
        for block in mmbg.subblocks:
            yield (
                mmbg.tag,
                block.tag,
                as_strided(
                    data[mmbg.offset + block.offset :],
                    (mmbg.count, block.size),
                    (mmbg.step if mmbg.count > 1 else 1, 1),
                    subok=True,
                    writeable=False,
                ),
            )


def decode_srvi(rawdata):
    print("SRVI shape:", rawdata.shape)
    return {
        "frm": (
            ("frame",),
            rawdata[:, 0:4].view("<u4")[:, 0],
            {"long_name": "data frame number"},
        ),
        "Tm": (("frame",), rawdata[:, 4:8].view("<u4")[:, 0]),
        "TPow": (
            ("frame",),
            rawdata[:, 8:12].view("<f4")[:, 0],
            {"long_name": "avg transmit power"},
        ),
        "NPw": (
            ("frame", "cocx"),
            rawdata[:, 12:20].view("<f4"),
            {"long_name": "noise power pin-mod in save position"},
        ),
        "CPw": (
            ("frame", "cocx"),
            rawdata[:, 20:28].view("<f4"),
            {"long_name": "noise power int. source"},
        ),
        "PS_Stat": (("frame",), rawdata[:, 28:32].view("<u4")[:, 0]),
        "RC_Err": (("frame",), rawdata[:, 32:36].view("<u4")[:, 0]),
        "TR_Err": (("frame",), rawdata[:, 36:40].view("<u4")[:, 0]),
        "dwSTAT": (("frame",), rawdata[:, 40:44].view("<u4")[:, 0]),
        "dwGRST": (("frame",), rawdata[:, 44:48].view("<u4")[:, 0]),
        "AzmPos": (("frame",), rawdata[:, 48:52].view("<f4")[:, 0]),
        "AzmVel": (("frame",), rawdata[:, 52:56].view("<f4")[:, 0]),
        "ElvPos": (("frame",), rawdata[:, 56:60].view("<f4")[:, 0]),
        "ElvVel": (("frame",), rawdata[:, 60:64].view("<f4")[:, 0]),
        "NorthAngle": (("frame",), rawdata[:, 64:68].view("<f4")[:, 0]),
        "time_milli": (("frame",), rawdata[:, 68:72].view("<u4")[:, 0]),
        "PD_DataQuality": (("frame",), rawdata[:, 72:76].view("<u4")[:, 0]),
        "LO_Frequency": (("frame",), rawdata[:, 76:80].view("<f4")[:, 0]),
        "DetuneFine": (("frame",), rawdata[:, 80:84].view("<f4")[:, 0]),
    }


def decode_moment(name):
    def _decode(rawdata):
        # TODO(ALL): HACK: this arbitrarily reduces the range dimension to 512 to fit with the IQ output
        return {
            name: (
                ("frame", "range", "cocx"),
                rawdata.view("<f4").reshape(rawdata.shape[0], -1, 2)[:, :512, :],
            )
        }

    return _decode


def decode_iq(rawdata):
    # TODO(ALL) HACK: the 256 (for nfft) just appears, it probably should be read from somewhere else in the data
    return {
        "FFTD": (
            ("frame", "range", "cocx", "fft", "iq"),
            rawdata.view("<i2").reshape(rawdata.shape[0], -1, 2, 256, 2),
        )
    }


decoders = {
    """
    Decoders for IQ data as in Meteorological Ka-Band Cloud Radar MIRA35 Manual,
    section 2.3.3.2 'Embedded chain type 2; Data chain'. Note these decoders are
    specific to the Ka radar currently in operation on HALO.
    (last checked: 13th Septermber 2024).
    """
    b"SRVI": decode_srvi,
    b"SNRD": decode_moment("SNRD"),
    b"VELD": decode_moment("VELD"),
    b"HNED": decode_moment("HNED"),
    b"RMSD": decode_moment("RMSD"),
    b"FFTD": decode_iq,  # TODO(ALL) HACK: FFTD may or may not be IQ data. This is configured in PPAR
}


def read_pds(filename, postprocess=True):
    """
    Converts data from a file called 'filename', into an xarray Dataset. Currently
    only functioning with geometry of pds files and decoders for IQ data of
    Ka radar currently operational on HALO (last checked: 13th Septermber 2024).

    Optimisation uses NumPy library memory-mapped array to avoid reading
    entirety of large binary file into main memory from disc when only (small)
    segment is desired.

    Parameters:
    filename (str): The path to the file containing the IQ data.
    postprocess (bool): Whether to apply post-processing to the dataset. Default is True.

    Returns:
    xarray.Dataset: The IQ data dataset.
    """
    data = np.memmap(filename)
    raw_arrays = list(extract_raw_arrays(data, get_geometry(data)))
    # TODO(ALL) add failure if same decoder key repeats (temporary) 
    ds = xr.Dataset(
        {
            k: v
            for _, tag, array in raw_arrays
            if tag in decoders
            for k, v in decoders[tag](array).items()
        }
    )
    if postprocess:
        ds = ds.pipe(postprocess_iq)
    return ds


def decode_time(ds):
    """
    Replaces 'Tm' and 'time_milli' variables in dataset 'ds' with decoded time.

    The function calculates the time by adding:
    - A base time of "1970-01-01"
    - 'Tm' multiplied by 1 second (expressed as 1,000,000,000 ns)
    - 'time_milli' multiplied by 0.001 (expressed as ns)

    The resulting time is then assigned to a new 'time' variable in the dataset.

    Parameters:
    ds (xarray.Dataset): The input dataset containing 'Tm' and 'time_milli'
                         variables.

    Returns:
    xarray.Dataset: The dataset with 'Tm' and 'time_milli' variables replaced by
                    new 'time' variable added.
    """
    time = (
        np.datetime64("1970-01-01")
        + ds.Tm * np.timedelta64(1000000000, "ns")
        + ds.time_milli * np.timedelta64(1000, "ns") # [sic] 'time_milli' is microseconds 
    )
    return ds.drop_vars(["Tm", "time_milli"]).assign(time=time)


def postprocess_iq(ds):
    # TODO(ALL): move to new file
    return ds.pipe(decode_time)


def main():
    '''
    Use an argument parser with positional argument for the filename to read IQ
    data from the file called 'filename' and decode the time.
    '''
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("filename")
    args = parser.parse_args()

    print(read_pds(args.filename).pipe(decode_time))


if __name__ == "__main__":
    exit(main())
