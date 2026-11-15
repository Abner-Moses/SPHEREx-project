// Plain-language copy. Keep claims here conservative and checkable; anything numeric that SIMBAD
// supplies (coordinates, parallax distances) is shown from the data, not typed in here.

export const LANDMARK_NOTES = {
  "Barnard's Star": 'A small, dim red star only about 6 light-years away. It has the largest apparent motion across the sky of any known star — exactly the kind of “shifting” that comparing two visits can reveal.',
  'Galactic Center': 'The middle of our Milky Way galaxy, roughly 26,000 light-years away. Dust hides it from ordinary telescopes, but infrared light passes through much better — one reason infrared surveys matter.',
  'Andromeda Galaxy': 'The nearest big spiral galaxy to ours, about 2.5 million light-years away. It is the most distant thing most people can see with the naked eye.',
  'Orion Nebula': 'A glowing cloud where new stars are being born, roughly 1,300 light-years away. You can spot it with the naked eye as the fuzzy middle “star” of Orion’s sword.',
  'Crab Nebula': 'The expanding wreckage of a star that exploded; its light reached Earth in the year 1054. About 6,500 light-years away.',
  Pleiades: 'The “Seven Sisters”: a young cluster of hot blue stars a few hundred light-years away.',
  'Large Magellanic Cloud': 'A small galaxy that orbits ours, about 160,000 light-years away, visible from the southern hemisphere.',
  'Small Magellanic Cloud': 'Another small companion galaxy of the Milky Way, around 200,000 light-years away.',
  Sirius: 'The brightest star in our night sky, only 8.6 light-years away.',
  Betelgeuse: 'A giant red star at Orion’s shoulder, hundreds of light-years away and so large it would swallow the orbit of Mars if placed where the Sun is.',
  Vega: 'One of the brightest stars in the northern sky, about 25 light-years away.',
  Polaris: 'The North Star. It sits almost exactly above Earth’s north pole, so it appears to hardly move while the rest of the sky turns.',
  'Alpha Centauri': 'The closest star system to the Sun, about 4.3 light-years away.',
}

export const GLOSSARY = {
  ra: 'Right ascension (RA): the sky’s east–west coordinate, like longitude on Earth. Measured in degrees (0–360) or hours (0–24); 1 hour = 15 degrees.',
  dec: 'Declination (Dec): the sky’s north–south coordinate, like latitude on Earth. +90° is straight above Earth’s north pole, −90° above the south pole.',
  infrared: 'Light with longer wavelengths than our eyes can see. It carries heat and passes through dust that blocks visible light.',
  ecliptic: 'The path the Sun appears to trace across the sky over a year. The planets and most asteroids stay close to this line.',
  galactic: 'The band of the Milky Way. Most of our galaxy’s stars lie close to this plane.',
  units: 'Display units: the backend rescales each image to a 0–255 brightness range before sending it. These numbers show relative brightness in the picture, not calibrated physical flux.',
  parallax: 'The tiny apparent shift of a nearby star as Earth orbits the Sun. The smaller the shift, the farther the star. It is how we measure real distances.',
  simulated: 'Simulated (mock) data is a generated test pattern. It looks like an image but contains no real sky.',
}
