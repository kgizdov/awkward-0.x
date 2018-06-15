#!/usr/bin/env python

# Copyright (c) 2018, DIANA-HEP
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import collections
import numbers

import numpy

import awkward.base
import awkward.util

class ChunkedArray(awkward.base.AwkwardArray):
    def __init__(self, chunks, writeable=True, appendable=True, appendsize=1024):
        self.chunks = chunks
        self.writeable = writeable
        self.appendable = appendable
        self.appendsize = appendsize

    @property
    def chunks(self):
        return self._chunks

    @chunks.setter
    def chunks(self, value):
        try:
            self._chunks = list(value)
        except TypeError:
            raise TypeError("chunks must be iterable")

    @property
    def writeable(self):
        return self._writeable

    @writeable.setter
    def writeable(self, value):
        self._writeable = bool(value)

    @property
    def appendable(self):
        return self._appendable

    @appendable.setter
    def appendable(self, value):
        self._appendable = bool(value)

    @property
    def appendsize(self):
        return self._appendsize

    @appendsize.setter
    def appendsize(self, value):
        if not isinstance(value, (numbers.Integral, numpy.integer)) or value <= 0:
            raise TypeError("appendsize must be a positive integer")
        self._appendsize = value

    def _chunkiterator(self):
        sofar = i = 0
        while i < len(self._chunks):
            if not isinstance(self._chunks[i], (awkward.base.AwkwardArray, numpy.ndarray)):
                self._chunks[i] = numpy.array(self._chunks[i])

            yield sofar, self._chunks[i]
            sofar += len(self._chunks[i])
            i += 1

    @property
    def dtype(self):
        for sofar, chunk in self._chunkiterator():
            if len(chunk) > 0:
                return numpy.dtype((chunk.dtype, getattr(chunk, "shape", (0,))[1:]))
        raise ValueError("chunks are empty; cannot determine dtype")

    @property
    def dimension(self):
        try:
            return self.dtype.shape
        except ValueError:
            raise ValueError("chunks are empty; cannot determine dimension")

    def __iter__(self):
        for chunk in self._chunks:
            for x in chunk:
                yield x

    def __str__(self):
        values = []
        for x in self:
            if len(values) == 7:
                return "[{0} ...]".format(" ".join(str(x) for x in values))
            values.append(x)
        return "[{0}]".format(" ".join(str(x) for x in values))

    def _slicedchunks(self, start, stop, step, tail):
        if step == 0:
            raise ValueError("slice step cannot be zero")
        elif step is None:
            step = 1

        slicedchunks = []
        localstep = 1 if step > 0 else -1
        for sofar, chunk in self._chunkiterator():
            if len(chunk) == 0:
                continue

            if step > 0:
                if start is None:
                    localstart = None
                elif start < sofar:
                    localstart = None
                elif sofar <= start < sofar + len(chunk):
                    localstart = start - sofar
                else:
                    continue

                if stop is None:
                    localstop = None
                elif stop <= sofar:
                    break
                elif sofar < stop < sofar + len(chunk):
                    localstop = stop - sofar
                else:
                    localstop = None

            else:
                if start is None:
                    localstart = None
                elif start < sofar:
                    break
                elif sofar <= start < sofar + len(chunk):
                    localstart = start - sofar
                else:
                    localstart = None

                if stop is None:
                    localstop = None
                elif stop < sofar:
                    localstop = None
                elif sofar <= stop < sofar + len(chunk):
                    localstop = stop - sofar
                else:
                    continue

            slicedchunk = chunk[(slice(localstart, localstop, localstep),) + tail]
            if len(slicedchunk) != 0:
                slicedchunks.append(slicedchunk)

        if step > 0:
            return slicedchunks
        else:
            return list(reversed(slicedchunks))

    def _zerolen(self):
        try:
            dtype = self.dtype
        except ValueError:
            return numpy.empty(0)
        else:
            return numpy.empty(0, dtype)

    def __getitem__(self, where):
        if not isinstance(where, tuple):
            where = (where,)
        head, tail = where[0], where[1:]

        if isinstance(head, (numbers.Integral, numpy.integer)):
            if head < 0:
                raise IndexError("negative indexes are not allowed in ChunkedArray")

            for sofar, chunk in self._chunkiterator():
                if sofar <= head < sofar + len(chunk):
                    return chunk[(head - sofar,) + tail]

            raise IndexError("index {0} out of bounds for length {1}".format(head, sofar + len(chunk)))

        elif isinstance(head, slice):
            start, stop, step = head.start, head.stop, head.step
            if (start is not None and start < 0) or (stop is not None and stop < 0):
                raise IndexError("negative indexes are not allowed in ChunkedArray")

            slicedchunks = self._slicedchunks(start, stop, step, tail)

            if len(slicedchunks) == 0:
                return self._zerolen()

            if len(slicedchunks) == 1:
                out = slicedchunks[0]
            else:
                out = numpy.concatenate(slicedchunks)

            if step is None or step == 1:
                return out
            else:
                return out[::abs(step)]

        else:
            head = numpy.array(head, copy=False)
            if len(head.shape) == 1 and issubclass(head.dtype.type, numpy.integer):
                if len(head) == 0:
                    return self._zerolen()

                if (head < 0).any():
                    raise IndexError("negative indexes are not allowed in ChunkedArray")
                maxindex = head.max()

                out = None
                for sofar, chunk in self._chunkiterator():
                    if len(chunk) == 0:
                        continue
                    if out is None:
                        out = numpy.empty(len(head), dtype=numpy.dtype((chunk.dtype, chunk.shape[1:])))

                    indexes = head - sofar
                    mask = (indexes >= 0)
                    numpy.bitwise_and(mask, (indexes < len(chunk)), mask)
                    masked = indexes[mask]
                    if len(masked) != 0:
                        out[(mask,) + tail] = chunk[(masked,) + tail]

                    if sofar + len(chunk) > maxindex:
                        break

                if maxindex >= sofar + len(chunk):
                    raise IndexError("index {0} out of bounds for length {1}".format(maxindex, sofar + len(chunk)))
                return out[(slice(None),) + tail]

            elif len(head.shape) == 1 and issubclass(head.dtype.type, (numpy.bool, numpy.bool_)):
                numtrue = numpy.count_nonzero(head)

                out = None
                this = next = 0
                for sofar, chunk in self._chunkiterator():
                    if len(chunk) == 0:
                        continue
                    if out is None:
                        out = numpy.empty(numtrue, dtype=numpy.dtype((chunk.dtype, chunk.shape[1:])))

                    submask = head[sofar : sofar + len(chunk)]

                    next += numpy.count_nonzero(submask)
                    out[(slice(this, next),) + tail] = chunk[(submask,) + tail]
                    this = next

                if len(head) != sofar + len(chunk):
                    raise IndexError("boolean index did not match indexed array along dimension 0; dimension is {0} but corresponding boolean dimension is {1}".format(sofar + len(chunk), len(head)))
                return out[(slice(None),) + tail]

            else:
                raise TypeError("cannot interpret shape {0}, dtype {1} as a fancy index or mask".format(head.shape, head.dtype))

    def __setitem__(self, where, what):
        if not self._writeable:
            raise ValueError("assignment destination is read-only")

        if not isinstance(where, tuple):
            where = (where,)
        head, tail = where[0], where[1:]

        if isinstance(head, (numbers.Integral, numpy.integer)):
            if head < 0:
                raise IndexError("negative indexes are not allowed in ChunkedArray")

            for sofar, chunk in self._chunkiterator():
                if sofar <= head < sofar + len(chunk):
                    chunk[(head - sofar,) + tail] = what
                    return

            raise IndexError("index {0} out of bounds for length {1}".format(head, sofar + len(chunk)))

        elif isinstance(head, slice):
            start, stop, step = head.start, head.stop, head.step
            if (start is not None and start < 0) or (stop is not None and stop < 0):
                raise IndexError("negative indexes are not allowed in ChunkedArray")

            carry = 0
            fullysliced = []
            for slicedchunk in self._slicedchunks(start, stop, step, tail):
                if step is not None and step != 1:
                    length = len(slicedchunk)
                    slicedchunk = slicedchunk[carry::abs(step)]
                    carry = (carry - length) % step
                fullysliced.append(slicedchunk)

            if isinstance(what, (collections.Sequence, numpy.ndarray, awkward.base.AwkwardArray)) and len(what) == 1:
                for slicedchunk in fullysliced:
                    slicedchunk[:] = what[0]
            elif isinstance(what, (collections.Sequence, numpy.ndarray, awkward.base.AwkwardArray)):
                if len(what) != sum(len(x) for x in fullysliced):
                    raise ValueError("cannot copy sequence with size {0} to array with dimension {1}".format(len(what), sum(len(x) for x in fullysliced)))
                this = next = 0
                for slicedchunk in fullysliced:
                    next += len(slicedchunk)
                    slicedchunk[:] = what[this:next]
                    this = next
            else:
                for slicedchunk in fullysliced:
                    slicedchunk[:] = what

        else:
            head = numpy.array(head, copy=False)
            if len(head.shape) == 1 and issubclass(head.dtype.type, numpy.integer):
                if isinstance(what, (collections.Sequence, numpy.ndarray, awkward.base.AwkwardArray)) and len(what) != 1:
                    if hasattr(what, "shape"):
                        whatshape = what.shape
                    else:
                        whatshape = (len(what),)
                    if (len(head),) + tail != whatshape:
                        raise ValueError("shape mismatch: value array of shape {0} could not be broadcast to indexing result of shape {1}".format(whatshape, (len(head),) + tail))

                if len(head) == 0:
                    return

                if (head < 0).any():
                    raise IndexError("negative indexes are not allowed in ChunkArray")
                maxindex = head.max()

                chunks = []
                offsets = []
                for sofar, chunk in self._chunkiterator():
                    chunks.append(chunk)
                    if len(offsets) == 0:
                        offsets.append(sofar)
                    offsets.append(offsets[-1] + len(chunk))

                    if sofar + len(chunk) > maxindex:
                        break

                if maxindex >= sofar + len(chunk):
                    raise IndexError("index {0} out of bounds for length {1}".format(maxindex, sofar + len(chunk)))

                if isinstance(what, (collections.Sequence, numpy.ndarray, awkward.base.AwkwardArray)) and len(what) == 1:
                    for chunk, offset in awkward.util.izip(chunks, offsets):
                        indexes = head - offset
                        mask = (indexes >= 0)
                        numpy.bitwise_and(mask, (indexes < len(chunk)), mask)
                        chunk[indexes[mask]] = what[0]

                elif isinstance(what, (collections.Sequence, numpy.ndarray, awkward.base.AwkwardArray)):
                    # must fill "self[where] = what" using the same order for where and what, with locations scattered among a list of chunks
                    # thus, Pythonic iteration is necessary
                    chunkindex = numpy.searchsorted(offsets, head, side="right") - 1
                    i = 0
                    for headi, chunki in awkward.util.izip(head, chunkindex):
                        chunks[chunki][headi - offsets[chunki]] = what[i]
                        i += 1

                else:
                    for chunk, offset in awkward.util.izip(chunks, offsets):
                        indexes = head - offset
                        mask = (indexes >= 0)
                        numpy.bitwise_and(mask, (indexes < len(chunk)), mask)
                        chunk[indexes[mask]] = what
                    
            elif len(head.shape) == 1 and issubclass(head.dtype.type, (numpy.bool, numpy.bool_)):
                submasks = []
                for sofar, chunk in self._chunkiterator():
                    submask = head[sofar : sofar + len(chunk)]
                    submasks.append((submask, chunk))

                if len(head) != sofar + len(chunk):
                    raise IndexError("boolean index did not match indexed array along dimension 0; dimension is {0} but corresponding boolean dimension is {1}".format(sofar + len(chunk), len(head)))

                if isinstance(what, (collections.Sequence, numpy.ndarray, awkward.base.AwkwardArray)) and len(what) == 1:
                    for submask, chunk in submasks:
                        chunk[submask] = what[0]

                elif isinstance(what, (collections.Sequence, numpy.ndarray, awkward.base.AwkwardArray)):
                    this = next = 0
                    for submask, chunk in submasks:
                        next += numpy.count_nonzero(submask)
                        chunk[submask] = what[this:next]
                        this = next

                else:
                    for submask, chunk in submasks:
                        chunk[submask] = what

            else:
                raise TypeError("cannot interpret shape {0}, dtype {1} as a fancy index or mask".format(head.shape, head.dtype))

class PartitionedArray(ChunkedArray):
    pass