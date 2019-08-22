
function ShowMorris(element,data,xkey,ykeys,labels)
{
	var lineColors=arguments[5]?arguments[5]:['red','green'];
	document.getElementById(element).innerHTML = '';
	return new Morris.Line({
		// ID of the element in which to draw the chart.
		element: element,
		// Chart data records -- each entry in this array corresponds to a point on
		// the chart.
		data: data,
		// The name of the data record attribute that contains x-values.
		xkey: xkey,
		// A list of names of data record attributes that contain y-values.
		ykeys: ykeys,
		// Labels for the ykeys -- will be displayed when you hover over the
		// chart.
		labels: labels,
        smooth:false,
        lineColors:lineColors,
        lineWidth:0.5,
        pointSize:0,
		hideHover:'auto'
	});
}

function ShowMorrisArea(element,data,xkey,ykeys,labels)
{
	document.getElementById(element).innerHTML = '';
	return new Morris.Area({
		// ID of the element in which to draw the chart.
		element: element,
		behaveLikeLine: true,
		// Chart data records -- each entry in this array corresponds to a point on
		// the chart.
		data: data,
		// The name of the data record attribute that contains x-values.
		xkey: xkey,
		// A list of names of data record attributes that contain y-values.
		ykeys: ykeys,
		// Labels for the ykeys -- will be displayed when you hover over the
		// chart.
		labels: labels,
        pointSize:0
	});
}

function ShowDonut(element, data)
{
	document.getElementById(element).innerHTML = '';
	Morris.Donut({
		element: element,
		data: data,
		resize: true,
		formatter: function (val) {
			return val + 'GB'
		},
	  	colors: ['#26A0DA', '#ACACAC']
	});
}