#!/usr/bin/python
# -*- coding: utf-8 -*-

# Nik4: Export image from mapnik
# Run it with -h to see the list of options
# Written by Ilya Zverev, licensed WTFPL

import mapnik
import sys, os, re, argparse, math

try:
	import cairo
	HAS_CAIRO = True
except ImportError:
	HAS_CAIRO = False

TILE_BUFFER = 128
IM_MONTAGE = 'montage'

p3857 = mapnik.Projection('+proj=merc +a=6378137 +b=6378137 +lat_ts=0.0 +lon_0=0.0 +x_0=0.0 +y_0=0 +k=1.0 +units=m +nadgrids=@null +no_defs +over')
p4326 = mapnik.Projection('+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs')
transform = mapnik.ProjTransform(p4326, p3857)

def layer_bbox(m, names, bbox=None):
	"""Calculate extent of given layers and bbox"""
	for layer in (l for l in m.layers if l.name in names):
		# it may as well be a GPX layer in WGS84
		p = mapnik.Projection(layer.srs)
		lbbox = layer.envelope().inverse(p).forward(p3857)
		if bbox:
			bbox.expand_to_include(lbbox)
		else:
			bbox = lbbox
	return bbox

def filter_layers(m, lst):
	"""Leave only layers in list active, disable others"""
	for l in m.layers:
		l.active = l.name in lst

def select_layers(m, enable, disable):
	"""Enable and disable layers in corresponding lists"""
	for l in m.layers:
		if l.name in enable:
			l.active = True
		if l.name in disable:
			l.active = False

def prepare_ozi(mbbox, mwidth, mheight, name):
	"""Create georeferencing file for OziExplorer"""
	def deg(value, is_lon):
		degrees = math.floor(abs(value))
		minutes = (abs(value) - degrees) * 60
		return '{:4d},{:3.5F},{}'.format(int(round(degrees)), minutes, ('W' if is_lon else 'S') if value < 0 else ('E' if is_lon else 'N'))
	bbox = transform.backward(mbbox)
	points = "\n".join(['Point{:02d},xy,     ,     ,in, deg,    ,        ,N,    ,        ,E, grid,   ,           ,           ,N'.format(n) for n in range(3,31)])
	return '''OziExplorer Map Data File Version 2.2
Nik4
{}
1 ,Map Code,
WGS 84,WGS 84,   0.0000,   0.0000,WGS 84
Reserved 1
Reserved 2
Magnetic Variation,,,E
Map Projection,Mercator,PolyCal,No,AutoCalOnly,No,BSBUseWPX,No
Point01,xy,    0,    0,in, deg,{},{}, grid,   ,           ,           ,N
Point02,xy, {:4d}, {:4d},in, deg,{},{}, grid,   ,           ,           ,N
{}
Projection Setup,,,,,,,,,,
Map Feature = MF ; Map Comment = MC     These follow if they exist
Track File = TF      These follow if they exist
Moving Map Parameters = MM?    These follow if they exist
MM0,Yes
MMPNUM,4
MMPXY,1,0,0
'''.format(name, deg(bbox.maxy, False), deg(bbox.minx, True), mwidth - 1, mheight - 1, deg(bbox.miny, False), deg(bbox.maxx, True), points) \
	+ "MMPXY,2,{},0\n".format(mwidth) \
	+ "MMPXY,3,{},{}\n".format(mwidth, mheight) \
	+ "MMPXY,4,0,{}\n".format(mheight) \
	+ 'MMPLL,1,{:4.6f},{:4.6f}\n'.format(bbox.minx, bbox.maxy) \
	+ 'MMPLL,2,{:4.6f},{:4.6f}\n'.format(bbox.maxx, bbox.maxy) \
	+ 'MMPLL,3,{:4.6f},{:4.6f}\n'.format(bbox.maxx, bbox.miny) \
	+ 'MMPLL,4,{:4.6f},{:4.6f}\n'.format(bbox.minx, bbox.miny) \
	+ "MM1B,{}\n".format((mbbox.maxx - mbbox.minx) / mwidth * math.cos(math.radians(bbox.center().y))) \
	+ "MOP,Map Open Position,0,0\n" \
	+ "IWH,Map Image Width/Height,{},{}\n".format(mwidth, mheight)

def prepare_wld(bbox, mwidth, mheight):
	"""Create georeferencing world file"""
	pixel_x_size = (bbox.maxx - bbox.minx) / mwidth
	pixel_y_size = (bbox.maxy - bbox.miny) / mheight
	left_pixel_center_x = bbox.minx + pixel_x_size * 0.5
	top_pixel_center_y = bbox.maxy - pixel_y_size * 0.5
	return ''.join(["{:.8f}\n".format(n) for n in [pixel_x_size, 0.0, 0.0, -pixel_y_size, left_pixel_center_x, top_pixel_center_y]])

def parse_url(url, options):
	"""Parse map URL into options map"""
	lat = None
	lon = None
	zoom = None
	m = re.search(r'[#/=]([0-9]{1,2})/(-?[0-9]{1,2}\.[0-9]+)/(-?[0-9]{1,3}\.[0-9]+)', url)
	if m:
		zoom = int(m.group(1))
		lat = float(m.group(2))
		lon = float(m.group(3))
	else:
		m = re.search(r'lat=(-[0-9]{1,2}\.[0-9]+)', url, flags=re.IGNORECASE)
		if m:
			lat = float(m.group(1))
		m = re.search(r'lon=(-[0-9]{1,3}\.[0-9]+)', url, flags=re.IGNORECASE)
		if m:
			lon = float(m.group(1))
		m = re.search(r'zoom=([0-9]{1,2})', url, flags=re.IGNORECASE)
		if m:
			zoom = int(m.group(1))
	print lat, lon, zoom
	if zoom and not options.zoom:
		options.zoom = zoom
	if lat and lon and not options.center:
		options.center = [lon, lat]
	if not options.size and not options.size_px and not options.a and not options.fit and not options.bbox:
		options.size_px = [1280, 1024]

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='Tile-aware mapnik image renderer')
	parser.add_argument('-z', '--zoom', type=float, help='Target zoom level')
	parser.add_argument('-p', '--ppi', type=float, help='Pixels per inch (alternative to scale)')
	parser.add_argument('--factor', type=float, help='Scale factor (affects ppi, default=1)', default=1)
	parser.add_argument('-s', '--scale', type=float, help='Scale as in 1:100000 (specifying ppi is recommended)')
	parser.add_argument('-b', '--bbox', nargs=4, type=float, metavar=('Xmin', 'Ymin', 'Xmax', 'Ymax'), help='Bounding box')
	parser.add_argument('-a', type=int, choices=range(-7, 8), help='Paper format: -a 4 for landscape A4, -a -4 for portrait A4')
	parser.add_argument('-d', '--size', nargs=2, metavar=('W', 'H'), type=int, help='Target dimensions in mm')
	parser.add_argument('-x', '--size-px', nargs=2, metavar=('W', 'H'), type=int, help='Target dimensions in pixels')
	parser.add_argument('-m', '--margin', type=int, help='Amount in mm to reduce paper size')
	parser.add_argument('-c', '--center', nargs=2, metavar=('X', 'Y'), type=float, help='Center of an image')

	parser.add_argument('--fit', help='Fit layers in the map, comma-separated')
	parser.add_argument('--padding', type=int, help='Margin for layers in --fit (default=5), mm', default=5)
	parser.add_argument('--layers', help='Map layers to render, comma-separated')
	parser.add_argument('--add-layers', help='Map layers to include, comma-separated')
	parser.add_argument('--hide-layers', help='Map layers to hide, comma-separated')

	parser.add_argument('--url', help='URL of a map to center on')
	parser.add_argument('--ozi', type=argparse.FileType('w'), help='Generate ozi map file')
	parser.add_argument('--wld', type=argparse.FileType('w'), help='Generate world file')
	parser.add_argument('-t', '--tiles', type=int, choices=range(1, 13), default=1, help='Write N×N tiles, then join using imagemagick')
	parser.add_argument('-v', '--debug', action='store_true', default=False, help='Display calculated values')
	parser.add_argument('-f', '--format', dest='fmt', help='Target file format (by default looks at extension)')
	parser.add_argument('style', help='Style file for mapnik')
	parser.add_argument('output', help='Resulting image file')
	options = parser.parse_args()

	dim_mm = None
	scale = None
	size = None
	bbox = None

	if options.url:
		parse_url(options.url, options)

	# format should not be empty
	if options.fmt:
		fmt = options.fmt.lower()
	elif '.' in options.output:
		fmt = options.output.split('.')[-1].lower()
	else:
		fmt = 'png256'
	
	need_cairo = fmt in ['svg', 'pdf']
	
	# get image size in millimeters
	if options.a:
		dim_mm = [math.floor(1000 / 2**((2*abs(options.a) - 1) / 4.0) + 0.2), math.floor(1000 / 2**((2*(abs(options.a) + 1.0) - 1) / 4) + 0.2)]
		if options.a < 0:
			dim_mm = [dim_mm[1], dim_mm[0]]
	elif options.size:
		dim_mm = options.size
	if dim_mm and options.margin:
		dim_mm[0] = dim_mm[0] - options.margin * 2
		dim_mm[1] = dim_mm[1] - options.margin * 2

	# ppi and scale factor are the same thing
	if options.ppi:
		ppmm = options.ppi / 25.4
		scale_factor = options.ppi / 90.7
	else:
		scale_factor = options.factor
		ppmm = 90.7 / 25.4 * scale_factor
	
	# svg / pdf can be scaled only in cairo mode
	if scale_factor != 1 and need_cairo and not HAS_CAIRO:
		sys.stderr.write('Warning: install pycairo for using --factor or --ppi')
		scale_factor = 1
		ppmm = 90.7 / 25.4

	# convert physical size to pixels
	if options.size_px:
		size = options.size_px
	elif dim_mm:
		size = [int(round(dim_mm[0] * ppmm)), int(round(dim_mm[1] * ppmm))]

	# scale can be specified with zoom or with 1:NNN scale
	fix_scale = False
	if options.zoom:
		scale = 2 * 3.14159 * 6378137 / 2 ** (options.zoom + 8) / scale_factor
	elif options.scale:
		scale = options.scale * 0.00028 / scale_factor
		# Now we have to divide by cos(lat), but we might not know latitude at this point
		if options.center:
			scale = scale / math.cos(math.radians(center.y))
		elif options.bbox:
			scale = scale / math.cos(math.radians((options.bbox[3] + options.bbox[1]) / 2))
		else:
			fix_scale = True

	if options.bbox:
		bbox = options.bbox
	# all calculations are in EPSG:3857 projection (it's easier)
	if bbox:
		bbox = transform.forward(mapnik.Box2d(*bbox))

	# calculate bbox through center, zoom and target size
	if not bbox and options.center and size and scale:
		center = transform.forward(mapnik.Coord(*options.center))
		w = size[0] * scale / 2
		h = size[1] * scale / 2
		bbox = mapnik.Box2d(center.x-w, center.y-h, center.x+w, center.y+h)

	# for layer processing we need to create the Map object
	m = mapnik.Map(100, 100) # temporary size, will be changed before output
	mapnik.load_map(m, options.style)
	m.srs = p3857.params()

	# get bbox from layer extents
	if options.fit:
		bbox = layer_bbox(m, options.fit.split(','), bbox)
		# here's where we can fix scale, no new bboxes below
		if bbox and fix_scale:
			scale = scale / math.cos(math.radians(transform.backward(bbox.center()).y))
		# expand bbox with padding in mm
		if bbox and options.padding and (scale or size):
			if scale:
				tscale = scale
			else:
				tscale = min((bbox.maxx - bbox.minx) / size[0], (bbox.maxy - bbox.miny) / size[1])
			bbox.pad(options.padding * ppmm * tscale)

	# bbox should be specified by this point
	if not bbox:
		raise Exception('Bounding box was not specified in any way')

	# calculate pixel size from bbox and scale
	if not size:
		if scale:
			size = [int(round(abs(bbox.maxx - bbox.minx) / scale)), int(round(abs(bbox.maxy - bbox.miny) / scale))]
		else:
			raise Exception('Image dimensions or scale were not specified in any way')

	if need_cairo and options.tiles > 1:
		options.tiles = 1
	if max(size[0], size[1]) / options.tiles > 16384:
		raise Exception('Image size exceeds mapnik limit ({} > {}), use --tiles'.format(max(size[0], size[1]) / options.tiles, 16384))

	# add / remove some layers
	if options.layers:
		filter_layers(m, options.layers.split(','))
	if options.add_layers or options.hide_layers:
		select_layers(m, options.add_layers.split(',') if options.add_layers else [], options.hide_layers.split(',') if options.hide_layers else [])

	if options.debug:
		print 'scale={}'.format(scale)
		print 'scale_factor={}'.format(scale_factor)
		print 'size={},{}'.format(size[0], size[1])
		print 'bbox={}'.format(bbox)
		print 'bbox_wgs84={}'.format(transform.backward(bbox) if bbox else None)
		print 'layers=' + ','.join([l.name for l in m.layers if l.active])

	# generate metadata
	if options.ozi:
		options.ozi.write(prepare_ozi(bbox, size[0], size[1], options.output))
	if options.wld:
		options.wld.write(prepare_wld(bbox, size[0], size[1]))

	# export image
	m.aspect_fix_mode = mapnik.aspect_fix_mode.GROW_BBOX;
	m.resize(size[0], size[1])
	m.zoom_to_box(bbox)

	if need_cairo:
		if HAS_CAIRO:
			surface = cairo.SVGSurface(options.output, size[0], size[1]) if fmt == 'svg' else cairo.PDFSurface(options.output, size[0], size[1])
			mapnik.render(m, surface, scale_factor, 0, 0)
			surface.finish()
		else:
			mapnik.render_to_file(m, options.output, fmt)
	else:
		if options.tiles == 1:
			im = mapnik.Image(size[0], size[1])
			mapnik.render(m, im, scale_factor)
			im.save(options.output, fmt)
		else:
			# we cannot make mapnik calculate scale for us, so fixing aspect ratio outselves
			rdiff = (bbox.maxx-bbox.minx) / (bbox.maxy-bbox.miny) - size[0] / size[1]
			if rdiff > 0:
				bbox.height((bbox.maxx - bbox.minx) * size[1] / size[0])
			elif rdiff < 0:
				bbox.width((bbox.maxy - bbox.miny) * size[0] / size[1])
			scale = (bbox.maxx - bbox.minx) / size[0]
			width = max(32, int(math.ceil(1.0 * size[0] / options.tiles)))
			height = max(32, int(math.ceil(1.0 * size[1] / options.tiles)))
			m.resize(width, height)
			m.buffer_size = TILE_BUFFER
			tile_cnt = [int(math.ceil(1.0 * size[0] / width)), int(math.ceil(1.0 * size[1] / height))]
			if options.debug:
				print 'tile_count={},{}'.format(tile_cnt[0], tile_cnt[1])
				print 'tile_size={},{}'.format(width, height)
			tmp_tile = '{:02d}_{:02d}_{}'
			tile_files = []
			for row in range(0, tile_cnt[1]):
				for column in range(0, tile_cnt[0]):
					if options.debug:
						print 'tile={},{}'.format(row, column)
					m.zoom_to_box(mapnik.Box2d(bbox.minx + 1.0 * width * scale * column, bbox.maxy - 1.0 * height * scale * row, bbox.minx + 1.0 * width * scale * (column + 1), bbox.maxy - 1.0 * height * scale * (row + 1)))
					im = mapnik.Image(width if column < tile_cnt[0] - 1 else size[0] - width * (tile_cnt[0] - 1), height if row < tile_cnt[1] - 1 else size[1] - height * (tile_cnt[1] - 1))
					mapnik.render(m, im, scale_factor)
					tile_name = tmp_tile.format(row, column, options.output)
					im.save(tile_name, fmt)
					tile_files.append(tile_name)
			# join tiles and remove them if joining succeeded
			import subprocess
			result = subprocess.call([IM_MONTAGE, '-geometry', '+0+0', '-tile', '{}x{}'.format(tile_cnt[0], tile_cnt[1])] + tile_files + [options.output])
			if result == 0:
				for tile in tile_files:
					os.remove(tile)
